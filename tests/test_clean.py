"""Filtrado de calidad y máscara industrial · RF-P-04, tabla 8.1 (filas 8-10).

Este módulo tiene dos modos de fallo con signo opuesto y los dos son graves:

  - Filtrar de menos: la web muestra antorchas de refinería como incendios.
  - Filtrar de más: la máscara oculta un incendio real. El riesgo que la
    especificación marca como **grave** en la sección 11.

Por eso los tests de exclusión van siempre en pareja: se suprime lo que hay
sobre el foco industrial y se conserva lo que hay a 5 km.
"""

from __future__ import annotations

import geopandas as gpd
import pandas as pd
import pytest
from conftest import REFINERIA_PUERTOLLANO, make_hotspots, to_gdf

from incendios import clean as clean_mod
from incendios.config import EXCLUSION_BUFFER_M

LAT_REF, LON_REF = REFINERIA_PUERTOLLANO

# Un grado de latitud son ~111 km. 0.045 deg ~ 5 km: fuera del buffer de 1200 m.
GRADO_LAT_KM = 111.32


def _desplaza_km(lat: float, km: float) -> float:
    return lat + km / GRADO_LAT_KM


# --- tabla 8.1: hotspot sobre refinería / a 5 km ----------------------------


def test_suppresses_refinery():
    """RF-P-04: la antorcha de Puertollano no es un incendio forestal.

    Usa `config/exclusions.geojson` real, no una máscara de test: si alguien
    borra esa entrada del fichero, este test debe ponerse rojo.
    """
    hotspots = make_hotspots(LAT_REF, LON_REF, n=6, frp=25.0)

    out = clean_mod.clean(hotspots)

    assert len(out) == 0


def test_keeps_hotspot_5km_from_refinery():
    """El complemento del anterior. Sin este test, `clean` podría borrarlo todo
    y `test_suppresses_refinery` seguiría en verde.

    Se comprueba sobre `apply_exclusions` y no sobre `clean` para aislar la
    máscara: `clean` también deduplica, y 6 puntos coincidentes colapsan a 1 por
    una razón legítima que no tiene nada que ver con lo que aquí se prueba.
    """
    hotspots = to_gdf(make_hotspots(_desplaza_km(LAT_REF, 5.0), LON_REF, n=6, frp=25.0))

    assert len(clean_mod.apply_exclusions(hotspots)) == 6


def test_exclusion_boundary_is_the_configured_buffer():
    """Justo dentro del buffer se suprime; justo fuera sobrevive."""
    dentro = make_hotspots(_desplaza_km(LAT_REF, EXCLUSION_BUFFER_M / 1000 * 0.5), LON_REF)
    fuera = make_hotspots(_desplaza_km(LAT_REF, EXCLUSION_BUFFER_M / 1000 * 2.0), LON_REF)

    assert len(clean_mod.apply_exclusions(to_gdf(dentro))) == 0
    assert len(clean_mod.apply_exclusions(to_gdf(fuera))) == 1


def test_exclusions_absent_does_not_drop_anything(tmp_path, monkeypatch):
    """Sin fichero de máscara el pipeline sigue: mejor ruido que silencio."""
    monkeypatch.setattr(
        clean_mod, "load_exclusions", lambda *a, **k: None
    )
    hotspots = to_gdf(make_hotspots(LAT_REF, LON_REF, n=4))

    out = clean_mod.apply_exclusions(hotspots)

    assert len(out) == 4


def test_load_exclusions_missing_file_warns(tmp_path, caplog):
    with caplog.at_level("WARNING"):
        assert clean_mod.load_exclusions(tmp_path / "no-existe.geojson") is None
    assert any("máscara" in r.getMessage() for r in caplog.records)


# --- tabla 8.1: confianza baja ----------------------------------------------


def test_low_confidence_viirs_is_suppressed():
    """VIIRS usa confianza categórica: 'l' (low) no entra."""
    baja = make_hotspots(40.0, -6.0, n=5, confidence_raw="l", confidence_pct=20.0)

    assert len(clean_mod.filter_confidence(to_gdf(baja))) == 0


@pytest.mark.parametrize("nivel", ["n", "nominal", "h", "high"])
def test_nominal_and_high_confidence_survive(nivel):
    hotspots = make_hotspots(40.0, -6.0, n=3, confidence_raw=nivel)

    assert len(clean_mod.filter_confidence(to_gdf(hotspots))) == 3


def test_confidence_is_case_and_whitespace_tolerant():
    """Las respuestas de FIRMS no siempre vienen limpias."""
    hotspots = make_hotspots(40.0, -6.0, n=2, confidence_raw=" High ")

    assert len(clean_mod.filter_confidence(to_gdf(hotspots))) == 2


def test_modis_uses_numeric_threshold():
    """MODIS no tiene letras: se filtra por porcentaje contra MODIS_MIN_CONFIDENCE."""
    alta = make_hotspots(
        40.0, -6.0, n=2, instrument="MODIS", confidence_raw="78", confidence_pct=78.0
    )
    baja = make_hotspots(
        40.0, -6.0, n=2, instrument="MODIS", confidence_raw="12", confidence_pct=12.0
    )

    assert len(clean_mod.filter_confidence(to_gdf(alta))) == 2
    assert len(clean_mod.filter_confidence(to_gdf(baja))) == 0


# --- deduplicación espacio-temporal -----------------------------------------


def test_deduplicate_keeps_highest_frp():
    """NOAA-20 y NOAA-21 ven el mismo píxel con ~50 min de diferencia.

    Se conserva la detección de mayor FRP, no la primera que llegue: el FRP
    alimenta la escala de intensidad y quedarse con la menor la subestima.
    """
    fuerte = make_hotspots(40.0, -6.0, frp=90.0, source="VIIRS_NOAA20_NRT")
    flojo = make_hotspots(40.0, -6.0, frp=5.0, source="VIIRS_NOAA21_NRT")
    juntos = to_gdf(pd.concat([flojo, fuerte], ignore_index=True))

    out = clean_mod.deduplicate_spatial(juntos)

    assert len(out) == 1
    assert out["frp_mw"].iloc[0] == pytest.approx(90.0)


def test_deduplicate_keeps_distinct_pixels():
    """Dos píxeles separados más que la rejilla son dos detecciones."""
    a = make_hotspots(40.0, -6.0, frp=10.0)
    b = make_hotspots(_desplaza_km(40.0, 1.0), -6.0, frp=10.0)

    out = clean_mod.deduplicate_spatial(to_gdf(pd.concat([a, b], ignore_index=True)))

    assert len(out) == 2


def test_deduplicate_keeps_distinct_hours():
    """El mismo píxel en dos horas distintas es evolución del incendio, no ruido."""
    a = make_hotspots(40.0, -6.0, hours_ago=1.0)
    b = make_hotspots(40.0, -6.0, hours_ago=6.0)

    out = clean_mod.deduplicate_spatial(to_gdf(pd.concat([a, b], ignore_index=True)))

    assert len(out) == 2


# --- pipeline completo del módulo -------------------------------------------


def test_clean_composes_the_three_stages():
    """Una entrada con los tres casos a la vez sale con solo el hotspot bueno."""
    entrada = pd.concat(
        [
            make_hotspots(40.25, -6.60, n=3, spread_deg=0.004),  # incendio real
            make_hotspots(LAT_REF, LON_REF, n=4),  # antorcha industrial
            make_hotspots(39.10, -2.40, n=5, confidence_raw="l", confidence_pct=20.0),
        ],
        ignore_index=True,
    )

    out = clean_mod.clean(entrada)

    assert len(out) == 3
    assert out["latitude"].between(40.2, 40.3).all()
    assert isinstance(out, gpd.GeoDataFrame)
    assert out.crs.to_epsg() == 4326
    # `clean` no debe dejar rastro de sus columnas internas de trabajo.
    assert "excluded_by" not in out.columns
    assert "_dedup_key" not in out.columns
