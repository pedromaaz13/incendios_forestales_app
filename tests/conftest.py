"""Fábricas compartidas por la suite.

Ningún test toca la red. Los hotspots se generan de forma sintética con la misma
técnica que `scripts/smoke_test.py` —bloque gaussiano alrededor de un centro— y
las respuestas HTTP se sirven con `httpx.MockTransport`.

El reloj está congelado en `NOW`. Los módulos derivan estado de "ahora"
(`build_fires` marca activo/inactivo, `_finalize` rellena `reported_at`), así que
sin un instante fijo los tests fallarían según la hora a la que se ejecuten.
"""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest

from incendios.config import CRS_WGS84, Outputs
from incendios.firms import SCHEMA

FIXTURES = Path(__file__).parent / "fixtures"

# Instante de referencia. Cualquier antigüedad de los tests se expresa como
# desplazamiento sobre él, nunca sobre la hora real.
NOW = pd.Timestamp("2026-07-27T18:00:00Z")

# Puertollano: está en config/exclusions.geojson y es el caso que cita RF-P-04.
REFINERIA_PUERTOLLANO = (38.703, -4.092)


@pytest.fixture
def now() -> pd.Timestamp:
    return NOW


@pytest.fixture
def rng() -> np.random.Generator:
    # Semilla fija: un clustering que depende del azar no es un test, es una
    # moneda al aire.
    return np.random.default_rng(42)


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def load_json_fixture(name: str) -> dict:
    return json.loads(read_fixture(name))


# --- hotspots ---------------------------------------------------------------


def make_hotspots(
    lat: float,
    lon: float,
    n: int = 1,
    *,
    spread_deg: float = 0.0,
    hours_ago: float | list[float] = 1.0,
    frp: float = 20.0,
    confidence_raw: str = "n",
    confidence_pct: float = 60.0,
    instrument: str = "VIIRS",
    source: str = "VIIRS_NOAA20_NRT",
    area_key: str = "peninsula",
    rng: np.random.Generator | None = None,
) -> pd.DataFrame:
    """DataFrame con el esquema exacto que `firms._normalize` produce.

    `spread_deg=0` genera puntos coincidentes: útil para controlar la distancia
    exacta entre focos en los tests de clustering, donde un jitter aleatorio
    haría el resultado dependiente de la semilla.
    """
    rng = rng or np.random.default_rng(0)
    offsets = rng.normal(0, spread_deg, n) if spread_deg else np.zeros(n)
    offsets_lon = rng.normal(0, spread_deg, n) if spread_deg else np.zeros(n)

    if not isinstance(hours_ago, list):
        hours_ago = [hours_ago]
    ages = np.resize(np.array(hours_ago, dtype=float), n)

    return pd.DataFrame(
        {
            "latitude": lat + offsets,
            "longitude": lon + offsets_lon,
            "acq_dt": [NOW - pd.Timedelta(hours=float(h)) for h in ages],
            "satellite": "N",
            "instrument": instrument,
            "source": source,
            "area_key": area_key,
            "confidence_raw": confidence_raw,
            "confidence_pct": float(confidence_pct),
            "brightness_k": 335.0,
            "frp_mw": float(frp),
            "daynight": "D",
            "scan": 0.4,
            "track": 0.4,
        },
        columns=SCHEMA,
    )


def to_gdf(df: pd.DataFrame) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        df.copy(),
        geometry=gpd.points_from_xy(df["longitude"], df["latitude"]),
        crs=CRS_WGS84,
    )


@pytest.fixture
def hotspots_factory():
    return make_hotspots


# --- lado oficial y lado satelital ------------------------------------------


def make_official(
    rows: list[dict],
    *,
    default_precision_m: float = 500.0,
) -> pd.DataFrame:
    """Tabla oficial con el contrato de `sources.base.OFFICIAL_SCHEMA`.

    `merge.match` exige `reported_at` con tz y `precision_m` numérico; el resto
    de columnas viajan pero no participan en el emparejamiento.
    """
    base = {
        "source_id": "jcyl",
        "external_id": "X",
        "latitude": 40.0,
        "longitude": -6.0,
        "precision_m": default_precision_m,
        "reported_at": NOW,
        "status": "activo",
        "municipio": "Municipio",
        "provincia": "Provincia",
        "level": None,
        "resources": None,
        # Texto libre del operador. Hoy solo lo publica el 112 valenciano.
        "detalle": None,
        "raw_status": "Activo",
    }
    return pd.DataFrame([{**base, **r} for r in rows]).reset_index(drop=True)


def make_fires(rows: list[dict]) -> gpd.GeoDataFrame:
    """Clusters ya construidos, tal como `cluster.build_fires` los entrega."""
    base = {
        "fire_id": "f1",
        "area_key": "peninsula",
        "n_hotspots": 10,
        "frp_total_mw": 150.0,
        "status": "activo",
        "intensity": "media",
        "first_detected": NOW - pd.Timedelta(hours=6),
        "last_detected": NOW,
    }
    merged = [{**base, **r} for r in rows]
    df = pd.DataFrame(merged)
    return gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(df["longitude"], df["latitude"]),
        crs=CRS_WGS84,
    ).drop(columns=["latitude", "longitude"])


@pytest.fixture
def official_factory():
    return make_official


@pytest.fixture
def fires_factory():
    return make_fires


# --- salidas ----------------------------------------------------------------


@pytest.fixture
def tmp_outputs(tmp_path, monkeypatch):
    """Redirige todas las escrituras de `export` a un directorio temporal.

    `export.py` resuelve `OUTPUTS` y `HISTORY` desde sus globales en cada
    llamada, así que basta con sustituirlos en el espacio de nombres del módulo.
    Sin esto los tests ensuciarían `data/` del repo.
    """
    from incendios import export as export_mod

    out = tmp_path / "out"
    history = tmp_path / "history"
    out.mkdir(parents=True, exist_ok=True)
    history.mkdir(parents=True, exist_ok=True)

    outputs = Outputs(
        hotspots_geojson=out / "hotspots.geojson",
        fires_geojson=out / "fires.geojson",
        perimeters_geojson=out / "perimeters.geojson",
        hotspots_pmtiles=out / "hotspots.pmtiles",
        manifest=out / "manifest.json",
        history_dir=history,
    )

    monkeypatch.setattr(export_mod, "OUTPUTS", outputs)
    monkeypatch.setattr(export_mod, "HISTORY", history)
    return outputs
