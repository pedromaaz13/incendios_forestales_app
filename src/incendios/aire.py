"""Calidad del aire sobre la rejilla de contexto.

**Por qué esta capa y no las de radioafición.** El humo de un incendio afecta a
mucha más gente que el fuego: alcanza ciudades a decenas de kilómetros y es lo
que de verdad respira quien vive lejos del frente. Alguien en Madrid con un
incendio en la sierra no necesita saber dónde hay un repetidor LoRa; necesita
saber si el aire que respira está cargado.

Los datos vienen de CAMS (Copernicus Atmosphere Monitoring Service) servidos por
Open-Meteo. Es API pública documentada, sin registro ni clave.

**El AQI europeo no es un porcentaje.** Va de 0 a más de 100 y sus tramos están
definidos por la Agencia Europea de Medio Ambiente; pintarlo como una escala
lineal de color daría una lectura falsa. Los cortes de `NIVELES` son los
oficiales, no una interpolación bonita.

**Esta capa no dice de dónde viene el humo.** Un AQI alto en Valladolid puede ser
tráfico, una masa de aire sahariana o un incendio a 200 km. Atribuirlo al fuego
más cercano sería inventar una relación causal que el dato no soporta, así que la
interfaz lo presenta como contexto y nunca como consecuencia de un incendio
concreto.
"""

from __future__ import annotations

import logging

import geopandas as gpd
import httpx
import pandas as pd

from .config import CRS_WGS84
from .wind import GRID_POINTS

log = logging.getLogger(__name__)

OPEN_METEO_AIRE_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"

# CAMS publica cada hora y su resolución es de 11 km: pedirlo más a menudo no
# trae nada nuevo y carga un servicio gratuito sin motivo.
TTL_SECONDS = 1800

AIRE_SCHEMA = [
    "name",
    "latitude",
    "longitude",
    "aqi",
    "nivel",
    "pm2_5",
    "pm10",
    "co",
    "observed_at",
]

# Tramos del índice de calidad del aire europeo (EAQI) de la AEMA. El nombre va
# en la salida además del número porque "61" no le dice nada a nadie, y
# "desfavorable" sí.
NIVELES: tuple[tuple[float, str], ...] = (
    (20, "buena"),
    (40, "aceptable"),
    (60, "moderada"),
    (80, "desfavorable"),
    (100, "mala"),
    (float("inf"), "muy mala"),
)


def nivel(aqi: float | None) -> str:
    """Tramo cualitativo del EAQI. Sin dato devuelve cadena vacía, nunca 'buena'."""
    if aqi is None or not isinstance(aqi, (int, float)) or pd.isna(aqi):
        return ""
    for tope, etiqueta in NIVELES:
        if aqi < tope:
            return etiqueta
    return "muy mala"


def parse(payload: list[dict] | dict, points=GRID_POINTS) -> pd.DataFrame:
    """Normaliza la respuesta de Open-Meteo al esquema de calidad del aire."""
    if isinstance(payload, dict):
        payload = [payload]

    filas = []
    # `strict=False`: si la API devuelve menos bloques que puntos se ignoran los
    # que falten. La calidad del aire es contexto y no puede tumbar nada.
    for punto, bloque in zip(points, payload, strict=False):
        actual = bloque.get("current") or {}
        aqi = actual.get("european_aqi")
        if aqi is None:
            # Sin índice no hay nada que pintar. Un punto en blanco es más
            # honesto que un círculo verde puesto por defecto.
            log.warning("Aire: punto %s sin AQI, se descarta", punto[0])
            continue

        filas.append(
            {
                "name": punto[0],
                "latitude": bloque.get("latitude", punto[1]),
                "longitude": bloque.get("longitude", punto[2]),
                "aqi": float(aqi),
                "nivel": nivel(aqi),
                "pm2_5": _num(actual.get("pm2_5")),
                "pm10": _num(actual.get("pm10")),
                "co": _num(actual.get("carbon_monoxide")),
                "observed_at": actual.get("time"),
            }
        )

    if not filas:
        return pd.DataFrame(columns=AIRE_SCHEMA)
    return pd.DataFrame(filas)[AIRE_SCHEMA]


def to_gdf(df: pd.DataFrame) -> gpd.GeoDataFrame:
    if df.empty:
        return gpd.GeoDataFrame({c: [] for c in AIRE_SCHEMA}, geometry=[], crs=CRS_WGS84)
    return gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(df["longitude"], df["latitude"]),
        crs=CRS_WGS84,
    )


def fetch(client: httpx.Client | None = None, points=GRID_POINTS) -> gpd.GeoDataFrame:
    """Descarga el índice actual. Un fallo devuelve vacío, no tumba el pipeline."""
    propio = client is None
    client = client or httpx.Client(follow_redirects=True)
    try:
        params = {
            "latitude": ",".join(f"{p[1]}" for p in points),
            "longitude": ",".join(f"{p[2]}" for p in points),
            "current": "european_aqi,pm2_5,pm10,carbon_monoxide",
            "timezone": "UTC",
        }
        r = client.get(OPEN_METEO_AIRE_URL, params=params, timeout=30.0)
        r.raise_for_status()
        df = parse(r.json(), points)
        log.info("Calidad del aire: %d/%d puntos", len(df), len(points))
        return to_gdf(df)
    except Exception as exc:
        log.error("Calidad del aire no disponible: %s: %s", type(exc).__name__, exc)
        return to_gdf(pd.DataFrame(columns=AIRE_SCHEMA))
    finally:
        if propio:
            client.close()


def _num(value) -> float | None:
    return None if value is None else float(value)
