"""Filtrado de calidad y supresión de falsos positivos.

Este módulo es el que separa un visor usable de una nube de puntos alarmista.
VIIRS detecta cualquier anomalía térmica: antorchas de refinería, incineradoras,
hornos cerámicos, centrales térmicas y, en verano, reflejos especulares sobre
plástico de invernadero.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import geopandas as gpd
import pandas as pd

from .config import (
    CONFIG,
    CRS_METRIC_MAINLAND,
    CRS_WGS84,
    EXCLUSION_BUFFER_M,
    MODIS_MIN_CONFIDENCE,
    VIIRS_MIN_CONFIDENCE,
)

log = logging.getLogger(__name__)

EXCLUSIONS_PATH = CONFIG / "exclusions.geojson"


def to_gdf(df: pd.DataFrame) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        df.copy(),
        geometry=gpd.points_from_xy(df["longitude"], df["latitude"]),
        crs=CRS_WGS84,
    )


def filter_confidence(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    is_viirs = gdf["instrument"].str.upper().str.startswith("VIIRS")

    viirs_ok = is_viirs & gdf["confidence_raw"].str.strip().str.lower().isin(
        {c[0] for c in VIIRS_MIN_CONFIDENCE} | set(VIIRS_MIN_CONFIDENCE)
    )
    modis_ok = ~is_viirs & (gdf["confidence_pct"] >= MODIS_MIN_CONFIDENCE)

    keep = viirs_ok | modis_ok
    log.info("Confianza: %d/%d hotspots conservados", int(keep.sum()), len(gdf))
    return gdf[keep].copy()


def load_exclusions(path: Path = EXCLUSIONS_PATH) -> gpd.GeoDataFrame | None:
    """Carga la máscara de exclusión industrial.

    El fichero se construye de forma empírica: ejecuta el pipeline durante unas
    semanas, agrega hotspots por celda de ~500 m y marca las celdas con
    detecciones en más de ~60 días distintos del año. Un incendio forestal no
    arde 200 días en el mismo píxel; una refinería sí.
    """
    if not path.exists():
        log.warning("Sin máscara de exclusión en %s", path)
        return None

    gdf = gpd.read_file(path).to_crs(CRS_METRIC_MAINLAND)
    gdf["geometry"] = gdf.geometry.buffer(EXCLUSION_BUFFER_M)
    return gdf


def apply_exclusions(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    mask = load_exclusions()
    if mask is None or mask.empty:
        gdf["excluded_by"] = None
        return gdf

    projected = gdf.to_crs(CRS_METRIC_MAINLAND)
    joined = gpd.sjoin(
        projected, mask[["geometry", "name"]], how="left", predicate="within"
    )
    joined = joined[~joined.index.duplicated(keep="first")]

    gdf = gdf.copy()
    gdf["excluded_by"] = joined["name"].values

    dropped = gdf["excluded_by"].notna().sum()
    log.info("Exclusiones industriales: %d hotspots descartados", int(dropped))
    return gdf[gdf["excluded_by"].isna()].drop(columns=["excluded_by"])


def deduplicate_spatial(gdf: gpd.GeoDataFrame, grid_m: int = 200) -> gpd.GeoDataFrame:
    """Colapsa detecciones del mismo píxel por sensores distintos.

    NOAA-20 y NOAA-21 pasan con ~50 min de diferencia. Sin esto, un incendio
    grande aparece con el triple de puntos de los que le corresponden.
    """
    p = gdf.to_crs(CRS_METRIC_MAINLAND)
    key = (
        (p.geometry.x // grid_m).astype(int).astype(str)
        + "_"
        + (p.geometry.y // grid_m).astype(int).astype(str)
        + "_"
        + p["acq_dt"].dt.floor("1h").astype(str)
    )
    gdf = gdf.copy()
    gdf["_dedup_key"] = key.values

    # Conserva la detección de mayor FRP dentro de cada celda-hora.
    idx = gdf.sort_values("frp_mw", ascending=False).drop_duplicates("_dedup_key").index
    out = gdf.loc[idx].drop(columns=["_dedup_key"]).sort_values("acq_dt")

    log.info("Dedup espacio-temporal: %d -> %d", len(gdf), len(out))
    return out.reset_index(drop=True)


def clean(df: pd.DataFrame) -> gpd.GeoDataFrame:
    gdf = to_gdf(df)
    gdf = filter_confidence(gdf)
    gdf = apply_exclusions(gdf)
    gdf = deduplicate_spatial(gdf)
    return gdf


def write_empty_exclusions(path: Path = EXCLUSIONS_PATH) -> None:
    """Semilla vacía para que el pipeline arranque sin fichero previo."""
    path.write_text(
        json.dumps({"type": "FeatureCollection", "features": []}, indent=2),
        encoding="utf-8",
    )
