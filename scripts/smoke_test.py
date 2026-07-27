"""Prueba de humo sin red: genera hotspots sintéticos y ejercita el pipeline.

Simula tres escenarios que el pipeline debe distinguir:
  1. Un incendio grande con detecciones en varias pasadas.
  2. Un incendio pequeño de un solo píxel.
  3. Una antorcha de refinería (debe quedar suprimida por la máscara).

Uso:  PYTHONPATH=src python scripts/smoke_test.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from incendios import clean as clean_mod  # noqa: E402
from incendios import cluster as cluster_mod  # noqa: E402
from incendios import enrich as enrich_mod  # noqa: E402
from incendios import export as export_mod  # noqa: E402
from incendios.pipeline import setup_logging  # noqa: E402

rng = np.random.default_rng(42)
NOW = pd.Timestamp.now(tz="UTC").floor("h")


def block(lat, lon, n, spread_deg, hours_ago, frp, area_key="peninsula"):
    return pd.DataFrame(
        {
            "latitude": lat + rng.normal(0, spread_deg, n),
            "longitude": lon + rng.normal(0, spread_deg, n),
            "acq_dt": [NOW - pd.Timedelta(hours=h) for h in rng.choice(hours_ago, n)],
            "satellite": "N",
            "instrument": "VIIRS",
            "source": "VIIRS_NOAA20_NRT",
            "area_key": area_key,
            "confidence_raw": "n",
            "confidence_pct": 60.0,
            "brightness_k": rng.normal(330, 12, n),
            "frp_mw": rng.gamma(2.0, frp / 2.0, n),
            "daynight": "D",
            "scan": 0.4,
            "track": 0.4,
        }
    )


def main() -> None:
    setup_logging(verbose=False)

    df = pd.concat(
        [
            # Sierra de Gata: incendio grande, dos pasadas
            block(40.25, -6.60, 140, 0.012, [2, 3, 14, 15], frp=30),
            # Monte pequeño en Galicia: un puñado de píxeles
            block(42.40, -7.85, 4, 0.003, [4], frp=8),
            # Antorcha de la refinería de Puertollano: debe desaparecer
            block(38.703, -4.092, 6, 0.001, [1, 13], frp=25),
            # Detección de baja confianza: debe caer en el filtro
            block(39.10, -2.40, 10, 0.01, [5], frp=3).assign(
                confidence_raw="l", confidence_pct=20.0
            ),
        ],
        ignore_index=True,
    )

    print(f"entrada          : {len(df)} hotspots sintéticos")

    hotspots = clean_mod.clean(df)
    hotspots = cluster_mod.assign_fire_ids(hotspots)
    fires = enrich_mod.enrich_admin(cluster_mod.build_fires(hotspots))
    perims = cluster_mod.build_perimeters(hotspots)
    manifest = export_mod.export_all(hotspots, fires, perims)

    assert len(hotspots) > 0, "el pipeline se quedó sin hotspots"
    assert fires["fire_id"].is_unique, "fire_id duplicado en la tabla de incendios"
    assert len(perims) == len(fires), "desajuste entre perímetros e incendios"

    # La antorcha está dentro del buffer de exclusión de Puertollano.
    near_refinery = (
        (hotspots["latitude"].sub(38.703).abs() < 0.02)
        & (hotspots["longitude"].sub(-4.092).abs() < 0.02)
    ).sum()
    assert near_refinery == 0, "la máscara de exclusión no suprimió la antorcha"

    # Los hotspots de baja confianza no deben sobrevivir.
    assert (hotspots["confidence_pct"] < 30).sum() == 0, "pasó un hotspot de baja confianza"

    print("\nincendios detectados:")
    cols = ["fire_id", "n_hotspots", "frp_total_mw", "intensity", "status", "area_est_ha"]
    print(fires[cols].to_string(index=False))
    print(f"\nmanifest         : {manifest['fires_active']} activos, "
          f"latencia {manifest['data_age_minutes']} min")
    print("\nOK — todas las aserciones pasaron")


if __name__ == "__main__":
    main()
