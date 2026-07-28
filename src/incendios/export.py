"""Materialización de salidas.

Tres artefactos, tres consumidores:
  - GeoJSON  -> frontend, carga directa. Suficiente hasta ~20k features.
  - PMTiles  -> frontend a escala. Un solo fichero en object storage, servido
                por rangos HTTP. Elimina el backend de tiles por completo.
  - Parquet  -> histórico particionado por fecha, para análisis posterior con
                DuckDB / Databricks / Fabric.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess

import geopandas as gpd
import pandas as pd

from .config import HISTORY, OUTPUTS

log = logging.getLogger(__name__)

# Campos que viajan al navegador. Todo lo demás se queda en el Parquet: cada
# propiedad extra multiplica por el número de features en el GeoJSON.
# `instrument` viaja aunque cueste bytes: sin él el filtro de sensor de RF-F-09
# no puede distinguir VIIRS de MODIS y falla en silencio —apagar MODIS no hace
# nada y apagar VIIRS lo oculta todo—, y el manifiesto no puede publicar la
# antigüedad por familia de sensor, que es media razón de ser de este proyecto.
HOTSPOT_WEB_FIELDS = [
    "acq_dt",
    "frp_mw",
    "confidence_pct",
    "fire_id",
    "daynight",
    "instrument",
]
FIRE_WEB_FIELDS = [
    "fire_id",
    "status",
    "intensity",
    "n_hotspots",
    "frp_total_mw",
    "area_est_ha",
    "first_detected",
    "last_detected",
    "hours_since_last",
    "municipio",
    "provincia",
]


def _isoformat(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    gdf = gdf.copy()
    for col in gdf.columns:
        if pd.api.types.is_datetime64_any_dtype(gdf[col]):
            gdf[col] = gdf[col].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return gdf


def _write_geojson(gdf: gpd.GeoDataFrame, path, fields: list[str]) -> None:
    keep = [c for c in fields if c in gdf.columns] + ["geometry"]
    slim = _isoformat(gdf[keep])
    path.parent.mkdir(parents=True, exist_ok=True)
    slim.to_file(path, driver="GeoJSON")
    log.info("%s -> %d features (%.0f KB)", path.name, len(slim), path.stat().st_size / 1024)


def write_history(hotspots: gpd.GeoDataFrame) -> None:
    """Append idempotente al histórico, particionado por día de adquisición."""
    df = pd.DataFrame(hotspots.drop(columns="geometry"))
    for day, block in df.groupby(df["acq_dt"].dt.date):
        part = HISTORY / f"acq_date={day.isoformat()}"
        part.mkdir(parents=True, exist_ok=True)
        target = part / "part.parquet"

        if target.exists():
            block = pd.concat([pd.read_parquet(target), block], ignore_index=True)
            block = block.drop_duplicates(
                subset=["latitude", "longitude", "acq_dt", "source"]
            )
        block.to_parquet(target, index=False)

    log.info("Histórico actualizado en %s", HISTORY)


def write_pmtiles(geojson_path, pmtiles_path, layer: str, max_zoom: int = 12) -> bool:
    """Genera PMTiles con tippecanoe. Silencioso si tippecanoe no está instalado."""
    if shutil.which("tippecanoe") is None:
        log.warning("tippecanoe no encontrado; se omite la generación de PMTiles")
        return False

    cmd = [
        "tippecanoe",
        "-o", str(pmtiles_path),
        "--force",
        "-l", layer,
        "-z", str(max_zoom),
        "-Z", "4",
        "--drop-densest-as-needed",
        "--extend-zooms-if-still-dropping",
        str(geojson_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    log.info("PMTiles -> %s (%.0f KB)", pmtiles_path.name, pmtiles_path.stat().st_size / 1024)
    return True


def write_manifest(hotspots: gpd.GeoDataFrame, fires: gpd.GeoDataFrame) -> dict:
    """Metadatos de la ejecución.

    `data_age_minutes` se publica a propósito: estos datos tienen entre 1 y 3
    horas de latencia y ocultarlo es lo que convierte un visor en desinformación.
    """
    now = pd.Timestamp.now(tz="UTC")
    last = hotspots["acq_dt"].max() if len(hotspots) else None

    manifest = {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "last_detection_at": last.strftime("%Y-%m-%dT%H:%M:%SZ") if last is not None else None,
        "data_age_minutes": int((now - last).total_seconds() / 60) if last is not None else None,
        "hotspots": len(hotspots),
        "fires_total": len(fires),
        "fires_active": int((fires["status"] == "activo").sum()) if len(fires) else 0,
        "frp_total_mw": float(fires["frp_total_mw"].sum()) if len(fires) else 0.0,
        "sources": ["NASA FIRMS VIIRS (S-NPP, NOAA-20, NOAA-21)", "NASA FIRMS MODIS"],
        "disclaimer": (
            "Detecciones satelitales de anomalías térmicas. No son información "
            "oficial de emergencias. Para incidencias en curso, 112."
        ),
    }
    OUTPUTS.manifest.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest


def export_all(
    hotspots: gpd.GeoDataFrame,
    fires: gpd.GeoDataFrame,
    perimeters: gpd.GeoDataFrame,
) -> dict:
    _write_geojson(hotspots, OUTPUTS.hotspots_geojson, HOTSPOT_WEB_FIELDS)
    _write_geojson(fires, OUTPUTS.fires_geojson, FIRE_WEB_FIELDS)
    _write_geojson(perimeters, OUTPUTS.perimeters_geojson, ["fire_id", "hull_area_ha"])

    write_pmtiles(OUTPUTS.hotspots_geojson, OUTPUTS.hotspots_pmtiles, layer="hotspots")
    write_history(hotspots)

    manifest = write_manifest(hotspots, fires)
    log.info("Manifest: %s", json.dumps(manifest, ensure_ascii=False))
    return manifest
