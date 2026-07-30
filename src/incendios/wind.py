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

# Rejilla regular de ~0,75° sobre España, más las islas. Son 187 puntos en una
# sola petición a Open-Meteo, que los sirve sin problema.
#
# Se pasó de 41 puntos nombrados por capital a esta rejilla porque con 41 el
# campo interpolado era tan pobre que la capa animada resultaba invisible: no
# había estructura que enseñar. Subir la densidad del dibujo sin subir la de los
# datos habría sido inventar detalle. Con 0,75° hay ~55 km entre nodos, que es
# lo que el modelo de Open-Meteo resuelve de verdad.
#
# Los nombres dejan de ser topónimos y pasan a ser la coordenada: la rejilla no
# cae sobre ciudades y llamarla "Madrid" sería mentir sobre dónde se midió.


def _rejilla() -> tuple[tuple[str, float, float], ...]:
    puntos: list[tuple[str, float, float]] = []

    # Península y Baleares.
    lat = 36.0
    while lat <= 43.9:
        lon = -9.3
        while lon <= 4.4:
            puntos.append((f"{lat:.2f},{lon:.2f}", round(lat, 2), round(lon, 2)))
            lon += 0.75
        lat += 0.75

    # Canarias, con su propio recorrido: meterlas en el bucle anterior
    # arrastraría medio Atlántico.
    lat = 27.6
    while lat <= 29.5:
        lon = -18.2
        while lon <= -13.4:
            puntos.append((f"{lat:.2f},{lon:.2f}", round(lat, 2), round(lon, 2)))
            lon += 0.75
        lat += 0.75

    return tuple(puntos)


GRID_POINTS: tuple[tuple[str, float, float], ...] = _rejilla()


WIND_SCHEMA = [
    "name",
    "latitude",
    "longitude",
    "speed_kmh",
    "gusts_kmh",
    "direction_from_deg",
    "direction_to_deg",
    "cardinal_from",
    # Temperatura y humedad relativa. Viajan con el viento porque vienen en la
    # misma respuesta de la misma llamada: pedirlas aparte sería una segunda
    # petición a la misma API para el mismo instante y los mismos 230 puntos.
    #
    # Y se leen juntas: 38 ºC con 15 % de humedad y viento de 40 km/h es la
    # combinación que propaga un incendio, y ninguno de los tres números por
    # separado lo dice.
    "temp_c",
    "humedad_pct",
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
                "temp_c": _num(actual.get("temperature_2m")),
                "humedad_pct": _num(actual.get("relative_humidity_2m")),
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
            "current": (
                "wind_speed_10m,wind_direction_10m,wind_gusts_10m,"
                "temperature_2m,relative_humidity_2m"
            ),
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
