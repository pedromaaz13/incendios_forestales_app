"""Cortes de tráfico de la DGT · RF-F-11.

Fuente: la publicación DATEX II v3.7 del punto de acceso nacional de la DGT
(`nap.dgt.es`), el sitio donde la normativa europea obliga a publicar la
información de tráfico en tiempo real. XML público, sin clave, 47 provincias.

**Lo que hace esta capa distinta.** La DGT declara la causa de cada corte, y una
de las causas de su vocabulario es `forestFire`. Eso convierte esta fuente en la
única del proyecto que puede afirmar una relación con el fuego sin deducirla: no
es que haya un corte cerca de un incendio, es que la DGT dice que ese corte es
por un incendio forestal. Se marca en `por_incendio` y el frontend lo destaca,
porque es la información más accionable de todo el visor — quien tiene que salir
de una zona necesita saber por dónde no puede.

Con el resto de cortes se mantiene la disciplina de siempre: un accidente a 2 km
de un foco es una coincidencia y no se insinúa lo contrario.

**Qué se descarta.** El feed trae 937 incidencias y muchas no impiden pasar: un
"objeto en la calzada" sin corte declarado no cambia una evacuación. Se publican
las que cortan algo —vía, calzada o carril— más todas las de causa incendio,
aunque la DGT no haya rellenado el grado. Quedan 491 de 937; publicarlas todas
sumaría 250 puntos que enterrarían los 34 que de verdad importan.

**La antigüedad viaja con cada corte** por el mismo motivo que la del dato
satelital: el feed marca todo como `active`, incluidos registros de hace meses, y
quien lo mire debe poder juzgarlo.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

import geopandas as gpd
import httpx
import pandas as pd

from .config import CRS_WGS84

log = logging.getLogger(__name__)

DATEX_URL = "https://nap.dgt.es/datex2/v3/dgt/SituationPublication/datex2_v37.xml"

# El fichero pesa 4 MB y el cron corre cada 30 min. La DGT publica más a menudo,
# pero pedirlo cada pocos minutos carga un servicio público sin ganar nada.
TTL_SECONDS = 900

XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"

TRAFICO_SCHEMA = [
    "id",
    "causa",
    "detalle",
    "cierre",
    "por_incendio",
    "carretera",
    "pk",
    "municipio",
    "provincia",
    "comunidad",
    "latitude",
    "longitude",
    "desde",
    "actualizado",
    "edad_dias",
]

# Grados de corte, de más a menos grave. Lo que no está aquí —carriles
# estrechados, desviados -- deja pasar y no se publica.
CIERRES = {
    "roadClosed": "carretera cerrada",
    "carriagewayClosures": "calzada cortada",
    "laneClosures": "carril cortado",
}

# Vocabulario DATEX II traducido. Lo lee alguien que quiere saber si puede pasar,
# no un ingeniero de tráfico.
ETIQUETA_CAUSA = {
    "roadMaintenance": "obras",
    "accident": "accidente",
    "vehicleObstruction": "vehículo obstaculizando",
    "environmentalObstruction": "obstáculo natural",
    "obstruction": "obstáculo",
    "infrastructureDamageObstruction": "daño en la infraestructura",
    "abnormalTraffic": "tráfico anómalo",
    "poorEnvironment": "condiciones ambientales",
    "roadOrCarriagewayOrLaneManagement": "gestión de carriles",
}

ETIQUETA_DETALLE = {
    "forestFire": "incendio forestal",
    "rockfalls": "desprendimientos",
    "flooding": "inundación",
    "avalanches": "aludes",
    "objectOnTheRoad": "objeto en la calzada",
    "obstructionOnTheRoad": "obstáculo en la calzada",
    "roadworks": "obras",
    "slowTraffic": "tráfico lento",
    "heavyTraffic": "tráfico denso",
}

# El valor que la DGT usa para declarar que el corte es por un incendio forestal.
CAUSA_INCENDIO = "forestFire"


def _sin_ns(elemento) -> str:
    return re.sub(r"\{.*\}", "", elemento.tag)


def _texto(nodo, nombre: str) -> str | None:
    """Primer descendiente con ese nombre y texto no vacío.

    Si el elemento no lleva texto propio sino un hijo `value` se devuelve ese:
    DATEX II lo hace con varios campos y sin esto salen vacíos.
    """
    for hijo in nodo.iter():
        if _sin_ns(hijo) != nombre:
            continue
        if (hijo.text or "").strip():
            return hijo.text.strip()
        for nieto in hijo:
            if _sin_ns(nieto) == "value" and (nieto.text or "").strip():
                return nieto.text.strip()
    return None


def _detalle(rec) -> str | None:
    """Subtipo de la causa. Cada tipo lo guarda en un elemento distinto."""
    for campo in (
        "environmentalObstructionType",
        "obstructionType",
        "accidentType",
        "abnormalTrafficType",
        "roadMaintenanceType",
    ):
        valor = _texto(rec, campo)
        if valor:
            return valor
    return None


def _edad_dias(iso: str | None, ahora: datetime) -> float | None:
    if not iso:
        return None
    try:
        return round((ahora - datetime.fromisoformat(iso)).total_seconds() / 86400, 1)
    except (ValueError, TypeError):
        return None


def parse(xml: str, ahora: datetime | None = None) -> pd.DataFrame:
    """Extrae los cortes relevantes de una publicación DATEX II v3."""
    ahora = ahora or datetime.now(UTC)

    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        log.error("DATEX II ilegible: %s", exc)
        return pd.DataFrame(columns=TRAFICO_SCHEMA)

    filas, descartados = [], 0
    for rec in root.iter():
        if _sin_ns(rec) != "situationRecord":
            continue

        gestion = _texto(rec, "roadOrCarriagewayOrLaneManagementType")
        causa = _texto(rec, "causeType")
        detalle = _detalle(rec)

        # Se exige que corte algo, o que la causa sea un incendio. Un "objeto en
        # la calzada" sin corte declarado no impide evacuar, y publicarlo sumaría
        # 250 puntos que enterrarían los 34 que sí importan. Los de incendio
        # entran siempre, aunque la DGT no haya rellenado el grado de corte.
        if gestion not in CIERRES and detalle != CAUSA_INCENDIO:
            descartados += 1
            continue

        lat, lon = _texto(rec, "latitude"), _texto(rec, "longitude")
        if not lat or not lon:
            # Sin coordenadas no se puede pintar, y un corte sin sitio no informa.
            descartados += 1
            continue

        actualizado = _texto(rec, "situationRecordVersionTime")
        filas.append(
            {
                "id": rec.attrib.get("id") or _texto(rec, "situationRecordCreationReference"),
                "causa": ETIQUETA_CAUSA.get(causa, causa or "incidencia"),
                "detalle": ETIQUETA_DETALLE.get(detalle, detalle),
                "cierre": CIERRES.get(gestion),
                # La DGT declara la causa: aquí no se deduce nada.
                "por_incendio": detalle == CAUSA_INCENDIO,
                "carretera": _texto(rec, "roadName") or _texto(rec, "roadNumber"),
                "pk": _texto(rec, "kilometerPoint"),
                "municipio": _texto(rec, "municipality"),
                "provincia": _texto(rec, "province"),
                "comunidad": _texto(rec, "autonomousCommunity"),
                "latitude": float(lat),
                "longitude": float(lon),
                "desde": _texto(rec, "overallStartTime"),
                "actualizado": actualizado,
                "edad_dias": _edad_dias(actualizado, ahora),
            }
        )

    if not filas:
        log.info("DGT: sin cortes relevantes de %d incidencias", descartados)
        return pd.DataFrame(columns=TRAFICO_SCHEMA)

    df = pd.DataFrame(filas)[TRAFICO_SCHEMA]
    por_incendio = int(df["por_incendio"].sum())
    log.info(
        "DGT: %d cortes relevantes (%d por incendio forestal) · %d descartados",
        len(df), por_incendio, descartados,
    )
    return df


def to_gdf(df: pd.DataFrame) -> gpd.GeoDataFrame:
    if df.empty:
        return gpd.GeoDataFrame(
            {c: [] for c in TRAFICO_SCHEMA}, geometry=[], crs=CRS_WGS84
        )
    return gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(df["longitude"], df["latitude"]),
        crs=CRS_WGS84,
    )


def fetch(client: httpx.Client | None = None) -> gpd.GeoDataFrame:
    """Descarga los cortes. Un fallo devuelve vacío, no tumba el pipeline."""
    propio = client is None
    client = client or httpx.Client(follow_redirects=True)
    try:
        # User-Agent identificable: si a alguien de la DGT le extraña el
        # tráfico, que pueda ver quién es antes de bloquearlo.
        r = client.get(
            DATEX_URL,
            timeout=120.0,
            headers={
                "User-Agent": (
                    "incendios-es/1.0 "
                    "(+https://github.com/pedromaaz13/incendios_forestales_app)"
                )
            },
        )
        r.raise_for_status()
        return to_gdf(parse(r.text))
    except Exception as exc:
        log.error("Cortes de la DGT no disponibles: %s: %s", type(exc).__name__, exc)
        return to_gdf(pd.DataFrame(columns=TRAFICO_SCHEMA))
    finally:
        if propio:
            client.close()
