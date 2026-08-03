"""Uso del suelo por incendio · CORINE Land Cover 2018.

Para qué sirve, y es la razón de que merezca una fuente nueva: **separa el
incendio forestal de la quema agrícola**. Un «incendio» sobre cultivo en julio
es casi siempre rastrojo quemándose de forma controlada, y hoy se publica igual
que uno en monte. Eso infla el recuento y, sobre todo, entierra los que
importan entre los que no.

Fuente, sondeada el 03-08-2026 y **accesible sin registro**:

    https://image.discomap.eea.europa.eu/arcgis/rest/services/Corine/CLC2018_WM/MapServer/0/query

Es el servicio ArcGIS público de la Agencia Europea de Medio Ambiente. Soporta
`Query`, así que se consulta **por coordenada** en vez de descargar la capa
entera: CORINE de toda Europa son decenas de gigabytes y aquí hacen falta unas
decenas de puntos por ejecución.

**Lo que este módulo no hace: ocultar incendios.** La tentación es filtrar los
agrícolas y quitarlos del mapa, y sería un error — una quema de rastrojo que se
descontrola es exactamente cómo empiezan muchos incendios forestales. Se
etiqueta, no se esconde, y la decisión de mirarlo o no es de quien mira.
"""

from __future__ import annotations

import logging

import geopandas as gpd
import httpx
import pandas as pd

log = logging.getLogger(__name__)

CLC_URL = (
    "https://image.discomap.eea.europa.eu/arcgis/rest/services/"
    "Corine/CLC2018_WM/MapServer/0/query"
)

# Nomenclatura CORINE de nivel 3. Solo se traducen las clases que pueden arder;
# el resto se agrupa por su primer dígito, que es el nivel 1.
#
# Los códigos que empiezan por 3 son superficies forestales y seminaturales: son
# los que este visor existe para vigilar. Los que empiezan por 2 son agrícolas.
CLASES = {
    "311": ("Bosque de frondosas", "forestal"),
    "312": ("Bosque de coníferas", "forestal"),
    "313": ("Bosque mixto", "forestal"),
    "321": ("Pastizal natural", "forestal"),
    "322": ("Landas y matorral", "forestal"),
    "323": ("Matorral esclerófilo", "forestal"),
    "324": ("Matorral boscoso de transición", "forestal"),
    "331": ("Playas y dunas", "otro"),
    "332": ("Roquedo", "otro"),
    "333": ("Espacios con vegetación escasa", "forestal"),
    "334": ("Zonas quemadas", "forestal"),
    "335": ("Glaciares y nieves", "otro"),
    "211": ("Cultivo herbáceo de secano", "agrícola"),
    "212": ("Cultivo herbáceo de regadío", "agrícola"),
    "213": ("Arrozal", "agrícola"),
    "221": ("Viñedo", "agrícola"),
    "222": ("Frutal", "agrícola"),
    "223": ("Olivar", "agrícola"),
    "231": ("Pradera", "agrícola"),
    "241": ("Cultivo anual con permanente", "agrícola"),
    "242": ("Mosaico de cultivos", "agrícola"),
    "243": ("Cultivo con vegetación natural", "agrícola"),
    "244": ("Sistema agroforestal", "agrícola"),
}

# Nivel 1, para lo que no esté en la tabla de arriba.
NIVEL_1 = {
    "1": ("Superficie artificial", "urbano"),
    "2": ("Superficie agrícola", "agrícola"),
    "3": ("Superficie forestal o seminatural", "forestal"),
    "4": ("Zona húmeda", "otro"),
    "5": ("Superficie de agua", "otro"),
}

CAMPOS_SUELO = ["suelo_codigo", "suelo_clase", "suelo_tipo"]

TIMEOUT = 25.0


def _describir(codigo: str | None) -> tuple[str | None, str | None]:
    """Del código CORINE al nombre y a la categoría gruesa."""
    if not codigo:
        return None, None

    codigo = str(codigo).strip()
    if codigo in CLASES:
        return CLASES[codigo]

    # Un código nuevo o de nivel distinto no se descarta: se degrada al nivel 1,
    # que sigue diciendo lo que hace falta saber —si es monte o es cultivo—.
    nombre, tipo = NIVEL_1.get(codigo[:1], (None, None))
    if nombre:
        log.info("CORINE: código %s no está en la tabla, se usa el nivel 1", codigo)
    return nombre, tipo


def consultar(lat: float, lon: float, client: httpx.Client) -> str | None:
    """Código CORINE en un punto. `None` si no se puede saber."""
    try:
        r = client.get(CLC_URL, params={
            "geometry": f"{lon},{lat}",
            "geometryType": "esriGeometryPoint",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "Code_18",
            "returnGeometry": "false",
            "f": "json",
        }, timeout=TIMEOUT)
        r.raise_for_status()
        cuerpo = r.json()
    except Exception as exc:
        log.warning("CORINE: consulta fallida en (%.4f, %.4f): %s", lat, lon, exc)
        return None

    if "error" in cuerpo:
        log.warning("CORINE: %s", (cuerpo["error"] or {}).get("message"))
        return None

    features = cuerpo.get("features") or []
    if not features:
        return None
    return (features[0].get("attributes") or {}).get("Code_18")


def anadir_uso_del_suelo(
    incidents: gpd.GeoDataFrame, client: httpx.Client | None = None
) -> gpd.GeoDataFrame:
    """Etiqueta cada incendio con el uso del suelo donde cae.

    Una consulta por incendio. Con las decenas que hay en una ejecución es
    asumible, y evita descargar una capa europea de decenas de gigabytes para
    mirar unos pocos puntos.

    Un fallo de la consulta deja el campo nulo y sigue: el uso del suelo es
    contexto, no puede tumbar la publicación de un incendio real.
    """
    salida = incidents.copy()
    for campo in CAMPOS_SUELO:
        if campo not in salida.columns:
            salida[campo] = None

    if salida.empty:
        return salida

    propio = client is None
    client = client or httpx.Client(follow_redirects=True)
    try:
        codigos = [
            consultar(punto.y, punto.x, client) if punto is not None and not punto.is_empty
            else None
            for punto in salida.geometry
        ]
    finally:
        if propio:
            client.close()

    descritos = [_describir(c) for c in codigos]
    salida["suelo_codigo"] = codigos
    salida["suelo_clase"] = [d[0] for d in descritos]
    salida["suelo_tipo"] = [d[1] for d in descritos]

    conocidos = int(pd.Series(salida["suelo_tipo"]).notna().sum())
    if conocidos:
        reparto = pd.Series(salida["suelo_tipo"]).value_counts().to_dict()
        log.info(
            "Suelo: %d/%d incendios con uso conocido · %s",
            conocidos, len(salida), reparto,
        )
    return salida
