"""Viento sobre 35 puntos · RF-P-09.

Open-Meteo es API pública documentada, sin registro ni clave. La URL está en la
documentación oficial del servicio; no es un endpoint descubierto por ingeniería
inversa, así que no aplica el protocolo de `docs/descubrimiento-fuentes.md`.

**Convención de dirección.** Meteorología expresa la dirección del viento como
*de dónde viene*: 0° es viento del norte, soplando hacia el sur. Es lo contrario
de lo que interpreta cualquiera que no sea meteorólogo, y aquí la lectura errónea
tiene consecuencias —hacia dónde avanza el frente—. Se publican los dos valores:
`direction_from_deg` tal cual lo da la fuente y `direction_to_deg` ya girado 180°,
que es el que dibuja la flecha. La leyenda lo explica (RF-P-09).
"""

from __future__ import annotations

import logging

import geopandas as gpd
import httpx
import pandas as pd

from .config import CRS_WGS84

log = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# TTL de 15 min: el viento no cambia más rápido que eso a esta resolución, y
# pedir más a menudo a un servicio gratuito es abusar de él.
TTL_SECONDS = 900

# 35 puntos repartidos por la Península, Baleares y Canarias. No es una rejilla
# regular: se densifica donde hay más masa forestal y donde el relieve hace que
# el viento cambie en pocos kilómetros.
GRID_POINTS: tuple[tuple[str, float, float], ...] = (
    ("A Coruña", 43.37, -8.40),
    ("Lugo", 43.01, -7.56),
    ("Ourense", 42.34, -7.86),
    ("Oviedo", 43.36, -5.85),
    ("Santander", 43.46, -3.81),
    ("Bilbao", 43.26, -2.93),
    ("Pamplona", 42.82, -1.64),
    ("León", 42.60, -5.57),
    ("Burgos", 42.34, -3.70),
    ("Zamora", 41.50, -5.75),
    ("Valladolid", 41.65, -4.72),
    ("Soria", 41.76, -2.46),
    ("Salamanca", 40.97, -5.66),
    ("Ávila", 40.66, -4.70),
    ("Segovia", 40.95, -4.12),
    ("Zaragoza", 41.65, -0.89),
    ("Huesca", 42.14, -0.41),
    ("Lleida", 41.62, 0.62),
    ("Girona", 41.98, 2.82),
    ("Barcelona", 41.39, 2.17),
    ("Tarragona", 41.12, 1.25),
    ("Madrid", 40.42, -3.70),
    ("Guadalajara", 40.63, -3.16),
    ("Cuenca", 40.07, -2.13),
    ("Toledo", 39.86, -4.02),
    ("Cáceres", 39.48, -6.37),
    ("Badajoz", 38.88, -6.97),
    ("Ciudad Real", 38.99, -3.93),
    ("Albacete", 38.99, -1.86),
    ("València", 39.47, -0.38),
    ("Alacant", 38.35, -0.48),
    ("Murcia", 37.99, -1.13),
    ("Córdoba", 37.89, -4.78),
    ("Sevilla", 37.39, -5.98),
    ("Jaén", 37.77, -3.79),
    ("Granada", 37.18, -3.60),
    ("Málaga", 36.72, -4.42),
    ("Huelva", 37.26, -6.95),
    ("Palma", 39.57, 2.65),
    ("Las Palmas", 28.12, -15.43),
    ("Tenerife", 28.47, -16.25),
)

WIND_SCHEMA = [
    "name",
    "latitude",
    "longitude",
    "speed_kmh",
    "gusts_kmh",
    "direction_from_deg",
    "direction_to_deg",
    "cardinal_from",
    "observed_at",
]

_CARDINALES = (
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSO", "SO", "OSO", "O", "ONO", "NO", "NNO",
)


def cardinal(deg: float) -> str:
    """Punto cardinal de origen, en castellano (O de oeste, no W)."""
    return _CARDINALES[int((float(deg) % 360) / 22.5 + 0.5) % 16]


def to_direction_deg(from_deg: float) -> float:
    """Gira 180°: de 'viene del norte' a 'sopla hacia el sur'."""
    return (float(from_deg) + 180.0) % 360.0


def parse(payload: list[dict] | dict, points=GRID_POINTS) -> pd.DataFrame:
    """Normaliza la respuesta de Open-Meteo al esquema de viento.

    Con varias coordenadas la API devuelve una lista; con una sola, un objeto.
    Se aceptan las dos formas para que el mismo código sirva en ambos casos.
    """
    if isinstance(payload, dict):
        payload = [payload]

    filas = []
    # `strict=False`: si Open-Meteo devuelve menos bloques que puntos, se
    # ignoran los que faltan. El viento es contexto, no puede tumbar nada.
    for punto, bloque in zip(points, payload, strict=False):
        actual = bloque.get("current") or {}
        direccion = actual.get("wind_direction_10m")
        if direccion is None:
            log.warning("Viento: punto %s sin dirección, se descarta", punto[0])
            continue

        filas.append(
            {
                "name": punto[0],
                "latitude": bloque.get("latitude", punto[1]),
                "longitude": bloque.get("longitude", punto[2]),
                "speed_kmh": _num(actual.get("wind_speed_10m")),
                "gusts_kmh": _num(actual.get("wind_gusts_10m")),
                "direction_from_deg": float(direccion),
                "direction_to_deg": to_direction_deg(direccion),
                "cardinal_from": cardinal(direccion),
                "observed_at": actual.get("time"),
            }
        )

    if not filas:
        return pd.DataFrame(columns=WIND_SCHEMA)
    return pd.DataFrame(filas)[WIND_SCHEMA]


def to_gdf(df: pd.DataFrame) -> gpd.GeoDataFrame:
    if df.empty:
        return gpd.GeoDataFrame({c: [] for c in WIND_SCHEMA}, geometry=[], crs=CRS_WGS84)
    return gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(df["longitude"], df["latitude"]),
        crs=CRS_WGS84,
    )


def fetch(client: httpx.Client | None = None, points=GRID_POINTS) -> gpd.GeoDataFrame:
    """Descarga el viento actual. Un fallo devuelve vacío, no tumba el pipeline.

    El viento es contexto: sin él el mapa sigue siendo útil. Abortar la
    publicación de incendios porque no responde un servicio meteorológico sería
    desproporcionado.
    """
    propio = client is None
    client = client or httpx.Client(follow_redirects=True)
    try:
        params = {
            "latitude": ",".join(f"{p[1]}" for p in points),
            "longitude": ",".join(f"{p[2]}" for p in points),
            "current": "wind_speed_10m,wind_direction_10m,wind_gusts_10m",
            "wind_speed_unit": "kmh",
            "timezone": "UTC",
        }
        r = client.get(OPEN_METEO_URL, params=params, timeout=30.0)
        r.raise_for_status()
        df = parse(r.json(), points)
        log.info("Viento: %d/%d puntos", len(df), len(points))
        return to_gdf(df)
    except Exception as exc:
        log.error("Viento no disponible: %s: %s", type(exc).__name__, exc)
        return to_gdf(pd.DataFrame(columns=WIND_SCHEMA))
    finally:
        if propio:
            client.close()


def _num(value) -> float | None:
    return None if value is None else float(value)
