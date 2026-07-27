"""Configuración central del pipeline. Todo lo ajustable vive aquí."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
CONFIG = ROOT / "config"
OUT = DATA / "out"
RAW = DATA / "raw"
HISTORY = DATA / "history"

for _p in (OUT, RAW, HISTORY):
    _p.mkdir(parents=True, exist_ok=True)

# --- FIRMS -------------------------------------------------------------------

FIRMS_MAP_KEY = os.environ.get("FIRMS_MAP_KEY", "")
FIRMS_BASE = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"

# Sensores. VIIRS 375 m es la base; MODIS 1 km se conserva por continuidad
# histórica y porque a veces detecta antes en pasadas nocturnas.
FIRMS_SOURCES = (
    "VIIRS_SNPP_NRT",
    "VIIRS_NOAA20_NRT",
    "VIIRS_NOAA21_NRT",
    "MODIS_NRT",
)

# bbox = west,south,east,north. Canarias va aparte: si metes todo en un solo
# bbox arrastras medio Atlántico y Marruecos, y FIRMS cobra por área.
AREAS: dict[str, str] = {
    "peninsula": "-9.60,35.85,4.40,43.90",
    "canarias": "-18.30,27.55,-13.30,29.50",
    "baleares": "1.10,38.60,4.40,40.15",
}

DAY_RANGE = 3  # días hacia atrás por petición (máximo permitido: 10)

# --- Calidad -----------------------------------------------------------------

# VIIRS: confidence categórico. MODIS: 0-100.
VIIRS_MIN_CONFIDENCE = ("nominal", "high")
MODIS_MIN_CONFIDENCE = 30

# Radio en metros alrededor de cada polígono/punto de exclusión industrial.
EXCLUSION_BUFFER_M = 1200

# --- Clustering --------------------------------------------------------------


@dataclass(frozen=True)
class ClusterParams:
    """Parámetros de ST-DBSCAN.

    eps_m: dos hotspots del mismo incendio raramente distan más de ~1.5 km
        dentro de la misma pasada. Subirlo fusiona incendios vecinos.
    eps_hours: ventana temporal. 18 h cubre la pasada diurna + la nocturna.
    min_samples: 1 permite incendios de un solo píxel (habituales y reales).
    time_scale_m_per_hour: convierte horas en "metros equivalentes" para poder
        usar un DBSCAN euclídeo de 3 dimensiones en lugar de implementar
        ST-DBSCAN a mano. eps_m / eps_hours mantiene ambos ejes comparables.
    """

    eps_m: float = 1500.0
    eps_hours: float = 18.0
    min_samples: int = 1

    @property
    def time_scale_m_per_hour(self) -> float:
        return self.eps_m / self.eps_hours


CLUSTER = ClusterParams()

# Ratio del concave hull (0 = muy cóncavo, 1 = convex hull).
HULL_RATIO = 0.35

# Un incendio deja de considerarse activo tras N horas sin detecciones nuevas.
ACTIVE_WINDOW_HOURS = 24

# --- Proyecciones ------------------------------------------------------------

CRS_WGS84 = 4326
# ETRS89 / UTM 30N sirve para península y Baleares con error aceptable.
# Canarias (REGCAN95 / UTM 28N) se proyecta aparte.
CRS_METRIC_MAINLAND = 25830
CRS_METRIC_CANARIAS = 4083

# --- Salidas -----------------------------------------------------------------


@dataclass(frozen=True)
class Outputs:
    hotspots_geojson: Path = OUT / "hotspots.geojson"
    fires_geojson: Path = OUT / "fires.geojson"
    perimeters_geojson: Path = OUT / "perimeters.geojson"
    hotspots_pmtiles: Path = OUT / "hotspots.pmtiles"
    manifest: Path = OUT / "manifest.json"
    history_dir: Path = HISTORY
    extra: dict = field(default_factory=dict)


OUTPUTS = Outputs()
