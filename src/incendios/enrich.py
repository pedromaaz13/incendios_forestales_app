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

# Distancia máxima a territorio español para dar un foco por nacional.
#
# Los bbox de FIRMS son rectángulos y arrastran país vecino: el de la península
# (-9.60, 35.85, 4.40, 43.90) cubre Portugal entero, el sur de Francia y parte
# de Argelia. Sin recortar, la mitad de los incendios "de España" resultaban ser
# portugueses o argelinos, y eso infla el recuento de una aplicación cuyo título
# dice España.
#
# El margen no es cero por dos razones: la geolocalización de VIIRS tiene un
# error de un par de kilómetros y un incendio costero puede caer mar adentro; y
# un fuego a pocos kilómetros de la raya sigue importándole a quien vive en
# Zamora o en Badajoz. A partir de ahí ya no es un incendio de España.
MARGEN_FRONTERA_M = 15_000


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


def clip_to_spain(
    gdf: gpd.GeoDataFrame,
    path: Path = MUNICIPIOS_PATH,
    margen_m: float = MARGEN_FRONTERA_M,
) -> tuple[gpd.GeoDataFrame, int]:
    """Descarta lo que caiga fuera de España y devuelve (conservados, descartados).

    Se aplica sobre los focos crudos y no sobre los incidentes ya formados, para
    que los recuentos del manifiesto también sean de España y para que el
    clustering no una un foco de Zamora con otro de Braganza en un mismo
    incendio.

    Sin capa municipal no se recorta nada: es preferible publicar de más —y
    decirlo— que descartar incendios reales por una capa que no está.
    """
    if not len(gdf):
        return gdf, 0

    if not path.exists():
        log.warning(
            "Sin capa de municipios en %s: no se puede recortar a España y los "
            "bbox de FIRMS arrastran Portugal, Francia y Argelia",
            path,
        )
        return gdf, 0

    muni = gpd.read_file(path)
    metrico = 25830
    puntos = gdf.to_crs(metrico)
    poligonos = muni.to_crs(metrico)[["geometry"]]

    # Dos pasadas: `within` resuelve la inmensa mayoría y es barato; la búsqueda
    # del vecino más próximo, que sí es cara, solo se hace sobre los que quedan
    # fuera de todo municipio.
    dentro = gpd.sjoin(puntos, poligonos, how="left", predicate="within")
    dentro = dentro[~dentro.index.duplicated(keep="first")]
    en_tierra = dentro["index_right"].notna().values

    resto = puntos[~en_tierra]
    cerca = en_tierra.copy()
    if len(resto):
        vecino = gpd.sjoin_nearest(resto, poligonos, how="left", distance_col="_d")
        vecino = vecino[~vecino.index.duplicated(keep="first")]
        cerca[~en_tierra] = (vecino["_d"] <= margen_m).values

    descartados = int((~cerca).sum())
    if descartados:
        log.info(
            "Recorte a España: %d focos descartados por estar a más de %.0f km "
            "de territorio español (los bbox de FIRMS cubren Portugal, Francia "
            "y el norte de África)",
            descartados, margen_m / 1000,
        )
    return gdf[cerca].copy(), descartados
