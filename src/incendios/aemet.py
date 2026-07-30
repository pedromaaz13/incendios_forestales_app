"""Avisos oficiales de meteorología adversa · AEMET Meteoalerta (CAP 1.2).

Por qué esta fuente y no una inferencia propia. Se podría derivar "riesgo de
tormenta" de variables crudas —CAPE, humedad, viento— y quedaría un mapa
bonito. Pero sería nuestra opinión sobre el tiempo, y este proyecto no tiene
autoridad meteorológica. Los avisos CAP son la declaración del organismo
competente: cuando AEMET dice naranja, es naranja, y esa palabra ya tiene un
significado que la gente conoce de los telediarios.

Qué aporta a un visor de incendios:

  · **Viento** — el factor que decide si un incendio se propaga o se queda.
  · **Temperaturas máximas** — la ola de calor que lo precede.
  · **Tormentas** — el rayo es una causa real de ignición en verano.

Forma del dato, verificada contra la respuesta real del 29-07-2026:

  La API devuelve un sobre JSON con un enlace. El enlace devuelve un **TAR sin
  comprimir** (`application/x-gtar`) con un XML por zona y fenómeno — 447
  ficheros en la muestra. Cada XML es un `<alert>` de CAP 1.2 con **dos bloques
  `<info>`**, uno en `es-ES` y otro en `en-GB`, idénticos salvo el idioma.

  El nivel (`verde`/`amarillo`/`naranja`/`rojo`) **no** está en `<severity>`,
  que usa el vocabulario de CAP. Está en un `<parameter>` con
  `valueName = "AEMET-Meteoalerta nivel"`. Es la diferencia entre publicar
  "Severe" y publicar "naranja".

  El polígono viene en orden **lat,lon** —el de CAP— y GeoJSON quiere lon,lat.
  Invertirlo no es opcional: sin invertir, los avisos de España aparecen en
  Somalia y nadie se da cuenta porque el mapa sigue pintando algo.
"""

from __future__ import annotations

import io
import logging
import os
import tarfile
import xml.etree.ElementTree as ET
from datetime import datetime

import geopandas as gpd
import httpx
import pandas as pd
from shapely.geometry import Polygon

from .config import CRS_WGS84

log = logging.getLogger(__name__)

CAP_NS = {"cap": "urn:oasis:names:tc:emergency:cap:1.2"}

AEMET_BASE = "https://opendata.aemet.es/opendata/api"
AVISOS_PATH = "avisos_cap/ultimoelaborado/area/esp"

# Idioma que se conserva. Cada alerta trae es-ES y en-GB con el mismo contenido;
# quedarse con los dos duplicaría cada aviso en el mapa.
IDIOMA = "es-ES"

# Orden de gravedad de Meteoalerta. Se publica el número además del nombre para
# que el frontend pueda ordenar y colorear sin conocer el vocabulario español.
NIVELES = {"verde": 0, "amarillo": 1, "naranja": 2, "rojo": 3}

# Los avisos verdes significan "sin riesgo". Publicarlos llenaría el mapa de
# polígonos que dicen que no pasa nada, y enterrarían los que sí importan.
NIVEL_MINIMO = 1

# Fenómenos que afectan a un incendio. AEMET publica también costeros, aludes y
# nevadas, que en este visor son ruido: el `eventCode` empieza por estas siglas.
FENOMENOS_RELEVANTES = {
    "AT": "temperaturas máximas",
    "BT": "temperaturas mínimas",
    "PR": "lluvia",
    "NE": "nieve",
    "TO": "tormentas",
    "VI": "viento",
    "PV": "polvo en suspensión",
}

CAMPOS = [
    "id",
    "fenomeno",
    "fenomeno_codigo",
    "nivel",
    "nivel_orden",
    "zona",
    "zona_codigo",
    "titular",
    "descripcion",
    "probabilidad",
    "onset",
    "expires",
    "sent",
]


def _texto(nodo: ET.Element | None) -> str:
    return (nodo.text or "").strip() if nodo is not None else ""


def _parametro(info: ET.Element, nombre: str) -> str:
    """Valor de un `<parameter>` por su `valueName`.

    AEMET mete en parámetros lo que no cabe en el vocabulario estándar de CAP:
    el nivel de Meteoalerta, el umbral concreto y la probabilidad.
    """
    for par in info.findall("cap:parameter", CAP_NS):
        if _texto(par.find("cap:valueName", CAP_NS)) == nombre:
            return _texto(par.find("cap:value", CAP_NS))
    return ""


def _poligono(area: ET.Element) -> Polygon | None:
    """Convierte el `<polygon>` de CAP en geometría.

    CAP escribe los pares como `lat,lon` separados por espacios. GeoJSON los
    quiere al revés, así que se invierten aquí y no en el frontend: un error de
    orden produce un mapa que sigue pintando polígonos, solo que en el océano
    Índico, y eso no se detecta mirando si "hay datos".
    """
    bruto = _texto(area.find("cap:polygon", CAP_NS))
    if not bruto:
        return None

    puntos: list[tuple[float, float]] = []
    for par in bruto.split():
        try:
            lat, lon = (float(v) for v in par.split(","))
        except ValueError:
            continue
        puntos.append((lon, lat))

    # Un anillo necesita 4 vértices contando el de cierre. Con menos, Shapely
    # lanzaría y tumbaría el aviso entero por un polígono degenerado.
    if len(puntos) < 4:
        return None
    return Polygon(puntos)


def parse_alerta(xml: bytes | str) -> list[dict]:
    """Extrae los avisos publicables de un `<alert>` de CAP.

    Devuelve una lista y no un único registro porque una alerta puede cubrir
    varias áreas. Se descartan aquí, y no aguas abajo, los avisos verdes y los
    fenómenos que no afectan a un incendio: filtrar pronto evita construir
    geometrías que nadie va a mirar.
    """
    try:
        raiz = ET.fromstring(xml)
    except ET.ParseError as exc:
        log.warning("AEMET: alerta CAP ilegible, se omite (%s)", exc)
        return []

    identificador = _texto(raiz.find("cap:identifier", CAP_NS))
    enviado = _texto(raiz.find("cap:sent", CAP_NS))

    filas: list[dict] = []

    for info in raiz.findall("cap:info", CAP_NS):
        if _texto(info.find("cap:language", CAP_NS)) != IDIOMA:
            continue

        nivel = _parametro(info, "AEMET-Meteoalerta nivel").lower()
        orden = NIVELES.get(nivel)
        if orden is None or orden < NIVEL_MINIMO:
            continue

        # `AT;Temperaturas máximas` → código y nombre.
        codigo_bruto = _texto(info.find("cap:eventCode/cap:value", CAP_NS))
        codigo = codigo_bruto.split(";")[0].strip()
        if codigo not in FENOMENOS_RELEVANTES:
            continue

        for area in info.findall("cap:area", CAP_NS):
            geom = _poligono(area)
            if geom is None:
                continue

            zona_codigo = ""
            for geocode in area.findall("cap:geocode", CAP_NS):
                if _texto(geocode.find("cap:valueName", CAP_NS)) == "AEMET-Meteoalerta zona":
                    zona_codigo = _texto(geocode.find("cap:value", CAP_NS))

            filas.append({
                # El identificador de CAP es único por alerta, no por área. Se
                # combina con la zona para que dos áreas de la misma alerta no
                # colapsen en un solo registro al deduplicar.
                "id": f"{identificador}:{zona_codigo}" if zona_codigo else identificador,
                "fenomeno": FENOMENOS_RELEVANTES[codigo],
                "fenomeno_codigo": codigo,
                "nivel": nivel,
                "nivel_orden": orden,
                "zona": _texto(area.find("cap:areaDesc", CAP_NS)),
                "zona_codigo": zona_codigo,
                "titular": _texto(info.find("cap:headline", CAP_NS)),
                "descripcion": _texto(info.find("cap:description", CAP_NS)),
                "probabilidad": _parametro(info, "AEMET-Meteoalerta probabilidad"),
                "onset": _texto(info.find("cap:onset", CAP_NS)),
                "expires": _texto(info.find("cap:expires", CAP_NS)),
                "sent": enviado,
                "geometry": geom,
            })

    return filas


def parse_tar(bruto: bytes) -> gpd.GeoDataFrame:
    """Desempaqueta el TAR de avisos y devuelve la capa lista para publicar.

    Un XML corrupto no tumba el lote: se registra y se sigue. Con 447 ficheros,
    perder los 446 buenos por uno malo sería cambiar un fallo pequeño por uno
    grande, y el sistema quedaría sin avisos justo el día que más se publican.
    """
    filas: list[dict] = []
    ilegibles = 0

    # AEMET lo sirve sin comprimir, pero `r:*` acepta también gzip y bzip2 por
    # si cambian de formato sin avisar, que es lo que suele pasar.
    with tarfile.open(fileobj=io.BytesIO(bruto), mode="r:*") as tar:
        for miembro in tar.getmembers():
            if not miembro.isfile() or not miembro.name.endswith(".xml"):
                continue
            fichero = tar.extractfile(miembro)
            if fichero is None:
                continue
            try:
                filas.extend(parse_alerta(fichero.read()))
            except Exception as exc:  # un aviso ilegible no tumba el lote
                ilegibles += 1
                log.warning("AEMET: %s ilegible (%s)", miembro.name, exc)

    if ilegibles:
        log.warning("AEMET: %d avisos ilegibles de %d", ilegibles, len(filas) + ilegibles)

    if not filas:
        log.info("AEMET: sin avisos de nivel amarillo o superior")
        return _vacia()

    gdf = gpd.GeoDataFrame(pd.DataFrame(filas), geometry="geometry", crs=CRS_WGS84)

    # Una misma zona puede tener varios avisos del mismo fenómeno si AEMET
    # reemitió: se conserva el más grave, que es el criterio de `_worst_status`
    # de la fusión y por la misma razón: quedarse corto es el error caro.
    gdf = gdf.sort_values("nivel_orden", ascending=False).drop_duplicates(
        subset=["zona_codigo", "fenomeno_codigo"], keep="first"
    )

    log.info(
        "AEMET: %d avisos publicables · %s",
        len(gdf),
        ", ".join(f"{n}={c}" for n, c in gdf["nivel"].value_counts().items()),
    )
    return gdf.reset_index(drop=True)


def descargar(clave: str, cliente) -> bytes:
    """Sigue el sobre de AEMET hasta los datos.

    La API no devuelve los datos: devuelve un JSON con `estado` y una URL en
    `datos`. Tratar ese sobre como si fueran los datos es el fallo que tuvo esta
    misma sonda hasta que se corrigió.
    """
    sobre = cliente.get(f"{AEMET_BASE}/{AVISOS_PATH}", params={"api_key": clave})
    sobre.raise_for_status()

    cuerpo = sobre.json()
    estado = cuerpo.get("estado")
    if estado != 200:
        raise RuntimeError(f"AEMET devolvió estado {estado}: {cuerpo.get('descripcion')}")

    enlace = cuerpo.get("datos")
    if not enlace:
        raise RuntimeError("AEMET no incluyó enlace de datos en el sobre")

    datos = cliente.get(enlace)
    datos.raise_for_status()
    return datos.content


def vigentes(gdf: gpd.GeoDataFrame, ahora: datetime | None = None) -> gpd.GeoDataFrame:
    """Descarta los avisos ya expirados.

    AEMET publica el último boletín elaborado, que incluye avisos de días
    anteriores todavía en el fichero. Enseñar un aviso naranja de ayer como si
    estuviera vigente es exactamente el tipo de dato caducado que este proyecto
    publica con su edad o no publica.
    """
    if gdf.empty:
        return gdf

    ahora = ahora or datetime.now().astimezone()
    expira = pd.to_datetime(gdf["expires"], errors="coerce", utc=True)
    vivos = expira.isna() | (expira >= pd.Timestamp(ahora).tz_convert("UTC"))

    descartados = int((~vivos).sum())
    if descartados:
        log.info("AEMET: %d avisos expirados descartados", descartados)
    return gdf[vivos].reset_index(drop=True)


AVISOS_SCHEMA = CAMPOS


def fetch(client: httpx.Client | None = None) -> gpd.GeoDataFrame:
    """Descarga los avisos vigentes. Un fallo devuelve vacío, no tumba nada.

    Sin `AEMET_API_KEY` se devuelve la capa vacía en vez de lanzar: la clave es
    opcional y el visor tiene que seguir funcionando sin ella, igual que sigue
    funcionando cuando una comunidad no publica.
    """
    clave = os.environ.get("AEMET_API_KEY", "")
    if not clave:
        log.info("AEMET: sin AEMET_API_KEY, se omiten los avisos")
        return _vacia()

    propio = client is None
    client = client or httpx.Client(follow_redirects=True, timeout=120.0)
    try:
        return vigentes(parse_tar(descargar(clave, client)))
    except Exception as exc:
        log.error("Avisos de AEMET no disponibles: %s: %s", type(exc).__name__, exc)
        return _vacia()
    finally:
        if propio:
            client.close()


def _vacia() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        columns=[*CAMPOS, "geometry"], geometry="geometry", crs=CRS_WGS84
    )
