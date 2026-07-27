"""Enriquecimiento administrativo: municipio y provincia por spatial join.

Requiere un GeoJSON/GPKG de límites municipales en config/municipios.geojson.
Fuente recomendada: líneas límite municipales del IGN (Centro de Descargas,
capa `recintos_municipales_inspire_peninbal_etrs89`). Si no está, el pipeline
sigue funcionando y deja los campos a None: el enriquecimiento es opcional por
diseño para que el repo arranque sin descargas manuales.
"""

from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd

from .config import CONFIG, CRS_WGS84

log = logging.getLogger(__name__)

MUNICIPIOS_PATH = CONFIG / "municipios.geojson"

# Nombres de columna habituales según la fuente. Se prueba en orden.
NAME_CANDIDATES = ("NAMEUNIT", "nombre", "NOMBRE", "municipio", "name")
PROV_CANDIDATES = ("provincia", "PROVINCIA", "CODNUT3", "nut3")


def _pick(gdf: gpd.GeoDataFrame, candidates: tuple[str, ...]) -> str | None:
    for c in candidates:
        if c in gdf.columns:
            return c
    return None


def enrich_admin(fires: gpd.GeoDataFrame, path: Path = MUNICIPIOS_PATH) -> gpd.GeoDataFrame:
    fires = fires.copy()

    if not path.exists():
        log.warning("Sin capa de municipios en %s; se omite el enriquecimiento", path)
        fires["municipio"] = None
        fires["provincia"] = None
        return fires

    muni = gpd.read_file(path).to_crs(CRS_WGS84)
    name_col = _pick(muni, NAME_CANDIDATES)
    prov_col = _pick(muni, PROV_CANDIDATES)

    if name_col is None:
        log.error("La capa de municipios no tiene columna de nombre reconocible")
        fires["municipio"] = None
        fires["provincia"] = None
        return fires

    cols = ["geometry", name_col] + ([prov_col] if prov_col else [])
    joined = gpd.sjoin(fires, muni[cols], how="left", predicate="within")
    joined = joined[~joined.index.duplicated(keep="first")]

    fires["municipio"] = joined[name_col].values
    fires["provincia"] = joined[prov_col].values if prov_col else None

    matched = fires["municipio"].notna().sum()
    log.info("Geocoding inverso: %d/%d incendios localizados", int(matched), len(fires))
    return fires
