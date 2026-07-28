"""Calidad del aire.

El riesgo de esta capa no es técnico, es de lectura: un círculo verde donde no
hay dato, o un tramo de color que no coincide con el que publica la Agencia
Europea de Medio Ambiente, harían que alguien concluyese que puede salir a la
calle cuando no debería.
"""

from __future__ import annotations

import httpx
import pytest

from incendios import aire


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _bloque(**cambios) -> dict:
    base = {
        "latitude": 40.42,
        "longitude": -3.70,
        "current": {
            "time": "2026-07-28T14:00",
            "european_aqi": 61.0,
            "pm2_5": 8.9,
            "pm10": 14.0,
            "carbon_monoxide": 151.0,
        },
    }
    base["current"].update(cambios.pop("current", {}))
    base.update(cambios)
    return base


# --- tramos del índice europeo -----------------------------------------------


@pytest.mark.parametrize(
    ("aqi", "esperado"),
    [
        (0, "buena"),
        (19.9, "buena"),
        (20, "aceptable"),
        (39.9, "aceptable"),
        (40, "moderada"),
        (61, "desfavorable"),
        (80, "mala"),
        (100, "muy mala"),
        (250, "muy mala"),
    ],
)
def test_european_aqi_bands_match_the_official_cuts(aqi, esperado):
    """Los cortes son los de la AEMA, no una escala lineal inventada.

    El EAQI no es un porcentaje: pintarlo interpolado daría una lectura falsa
    justo en la franja donde cambia la recomendación sanitaria.
    """
    assert aire.nivel(aqi) == esperado


def test_missing_aqi_is_not_green():
    """Sin dato no se devuelve "buena". Un verde por defecto donde no hay
    medición es exactamente el error que hace que alguien salga a correr."""
    assert aire.nivel(None) == ""


def test_nan_is_not_green():
    import math

    assert aire.nivel(math.nan) == ""


# --- parseo ------------------------------------------------------------------


def test_parse_normalises_the_schema():
    df = aire.parse([_bloque()], points=aire.GRID_POINTS[:1])

    assert list(df.columns) == aire.AIRE_SCHEMA
    fila = df.iloc[0]
    assert fila["aqi"] == 61.0
    assert fila["nivel"] == "desfavorable"
    assert fila["pm2_5"] == 8.9
    assert fila["name"] == aire.GRID_POINTS[0][0]


def test_parse_accepts_a_single_object_response():
    """Con una sola coordenada Open-Meteo devuelve un objeto, no una lista."""
    assert len(aire.parse(_bloque(), points=aire.GRID_POINTS[:1])) == 1


def test_points_without_index_are_dropped(caplog):
    """Un punto sin AQI no se pinta: en blanco es más honesto que en verde."""
    payload = [_bloque(), _bloque(current={"european_aqi": None})]

    with caplog.at_level("WARNING"):
        df = aire.parse(payload, points=aire.GRID_POINTS[:2])

    assert len(df) == 1
    assert any("sin AQI" in r.getMessage() for r in caplog.records)


def test_missing_particulates_are_none_not_zero():
    """PM2.5 ausente no es PM2.5 de cero."""
    df = aire.parse([_bloque(current={"pm2_5": None})], points=aire.GRID_POINTS[:1])

    assert df["pm2_5"].iloc[0] is None


def test_parse_empty_payload_keeps_schema():
    df = aire.parse([], points=aire.GRID_POINTS[:1])

    assert df.empty
    assert list(df.columns) == aire.AIRE_SCHEMA


# --- descarga ----------------------------------------------------------------


def test_fetch_returns_geodataframe():
    payload = [_bloque() for _ in aire.GRID_POINTS]
    with _client(lambda r: httpx.Response(200, json=payload)) as client:
        gdf = aire.fetch(client)

    assert len(gdf) == len(aire.GRID_POINTS)
    assert gdf.crs.to_epsg() == 4326


def test_fetch_failure_returns_empty_without_raising(caplog):
    """La calidad del aire es contexto: sin ella el mapa sigue sirviendo, y
    abortar la publicación de incendios por un servicio meteorológico gratuito
    sería desproporcionado."""
    with (
        caplog.at_level("ERROR"),
        _client(lambda r: httpx.Response(503, text="unavailable")) as client,
    ):
        gdf = aire.fetch(client)

    assert gdf.empty
    assert any("no disponible" in r.getMessage() for r in caplog.records)


def test_fetch_malformed_json_returns_empty():
    with _client(lambda r: httpx.Response(200, text="<html>nope</html>")) as client:
        assert aire.fetch(client).empty


def test_fetch_requests_the_documented_parameters():
    capturada = {}

    def handler(request):
        capturada["url"] = str(request.url)
        return httpx.Response(200, json=[_bloque() for _ in aire.GRID_POINTS])

    with _client(handler) as client:
        aire.fetch(client)

    assert "european_aqi" in capturada["url"]
    assert "pm2_5" in capturada["url"]
    assert "timezone=UTC" in capturada["url"]


def test_shares_the_wind_grid():
    """Misma rejilla que el viento: son las dos capas de contexto meteorológico
    y cuadrarlas permite leerlas juntas sin interpolar entre puntos distintos."""
    from incendios import wind

    assert aire.GRID_POINTS is wind.GRID_POINTS
