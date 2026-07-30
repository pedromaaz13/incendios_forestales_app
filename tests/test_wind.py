"""Viento · RF-P-09.

El requisito con más carga de diseño del módulo es la convención de dirección.
Meteorología dice "viento del norte" para el que *viene* del norte y sopla hacia
el sur. Publicar ese ángulo tal cual y dibujar una flecha con él apunta el frente
en la dirección contraria a la real, y aquí eso no es un detalle estético.
"""

from __future__ import annotations

import httpx
import pandas as pd
import pytest

from incendios import wind


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _bloque(**kwargs) -> dict:
    base = {
        "latitude": 40.42,
        "longitude": -3.70,
        "current": {
            "time": "2026-07-27T18:00",
            "wind_speed_10m": 24.5,
            "wind_direction_10m": 0.0,
            "wind_gusts_10m": 41.0,
        },
    }
    base["current"].update(kwargs.pop("current", {}))
    base.update(kwargs)
    return base


# --- convención de dirección -------------------------------------------------


@pytest.mark.parametrize(
    ("viene_de", "sopla_hacia"),
    [(0.0, 180.0), (90.0, 270.0), (180.0, 0.0), (270.0, 90.0), (315.0, 135.0)],
)
def test_arrow_points_where_the_wind_blows_to(viene_de, sopla_hacia):
    """RF-P-09: la flecha apunta hacia donde sopla, no de donde viene."""
    assert wind.to_direction_deg(viene_de) == sopla_hacia


def test_both_directions_are_published():
    """Se publican los dos ángulos: el crudo de la fuente y el girado.

    Quien quiera el dato meteorológico lo tiene sin transformar, y el mapa dibuja
    el que no induce a error. Publicar solo uno obliga a elegir a quién confundir.
    """
    df = wind.parse([_bloque(current={"wind_direction_10m": 45.0})], points=wind.GRID_POINTS[:1])

    fila = df.iloc[0]
    assert fila["direction_from_deg"] == 45.0
    assert fila["direction_to_deg"] == 225.0


@pytest.mark.parametrize(
    ("grados", "esperado"),
    [(0, "N"), (45, "NE"), (90, "E"), (180, "S"), (270, "O"), (359, "N")],
)
def test_cardinal_is_in_spanish(grados, esperado):
    """'O' de oeste, no 'W'. La interfaz está en castellano."""
    assert wind.cardinal(grados) == esperado


def test_cardinal_handles_out_of_range_degrees():
    assert wind.cardinal(365) == "N"
    assert wind.cardinal(-90) == "O"


# --- parseo ------------------------------------------------------------------


def test_parse_normalises_the_schema():
    df = wind.parse([_bloque()], points=wind.GRID_POINTS[:1])

    assert list(df.columns) == wind.WIND_SCHEMA
    fila = df.iloc[0]
    assert fila["speed_kmh"] == 24.5
    assert fila["gusts_kmh"] == 41.0
    assert fila["name"] == wind.GRID_POINTS[0][0]


def test_parse_accepts_a_single_object_response():
    """Con una sola coordenada Open-Meteo devuelve un objeto, no una lista."""
    df = wind.parse(_bloque(), points=wind.GRID_POINTS[:1])

    assert len(df) == 1


def test_parse_skips_points_without_direction(caplog):
    """Un punto sin dirección no puede dibujar flecha: se descarta con aviso."""
    payload = [_bloque(), _bloque(current={"wind_direction_10m": None})]

    with caplog.at_level("WARNING"):
        df = wind.parse(payload, points=wind.GRID_POINTS[:2])

    assert len(df) == 1
    assert any("sin dirección" in r.getMessage() for r in caplog.records)


def test_parse_empty_payload_keeps_schema():
    df = wind.parse([], points=wind.GRID_POINTS[:1])

    assert df.empty
    assert list(df.columns) == wind.WIND_SCHEMA


def test_missing_gusts_is_none_not_zero():
    """Una ráfaga ausente no es una ráfaga de 0 km/h."""
    df = wind.parse(
        [_bloque(current={"wind_gusts_10m": None})], points=wind.GRID_POINTS[:1]
    )

    assert df["gusts_kmh"].iloc[0] is None


# --- descarga ----------------------------------------------------------------


def test_fetch_returns_geodataframe():
    payload = [_bloque() for _ in wind.GRID_POINTS]
    handler = lambda request: httpx.Response(200, json=payload)

    with _client(handler) as client:
        gdf = wind.fetch(client)

    assert len(gdf) == len(wind.GRID_POINTS)
    assert gdf.crs.to_epsg() == 4326
    assert gdf.geometry.is_valid.all()


def test_fetch_failure_returns_empty_without_raising(caplog):
    """El viento es contexto: sin él el mapa sigue siendo útil.

    Abortar la publicación de incendios porque no responde un servicio
    meteorológico gratuito sería desproporcionado.
    """
    handler = lambda request: httpx.Response(503, text="unavailable")

    with caplog.at_level("ERROR"), _client(handler) as client:
        gdf = wind.fetch(client)

    assert gdf.empty
    assert list(gdf.columns)[:-1] == wind.WIND_SCHEMA
    assert any("Viento no disponible" in r.getMessage() for r in caplog.records)


def test_fetch_malformed_json_returns_empty():
    handler = lambda request: httpx.Response(200, text="<html>nope</html>")

    with _client(handler) as client:
        assert wind.fetch(client).empty


def test_fetch_requests_the_documented_parameters():
    """Se piden km/h y UTC explícitamente: los valores por defecto de la API
    dependen de la localización de quien llama."""
    capturada = {}

    def handler(request):
        capturada["url"] = str(request.url)
        return httpx.Response(200, json=[_bloque() for _ in wind.GRID_POINTS])

    with _client(handler) as client:
        wind.fetch(client)

    assert "wind_speed_unit=kmh" in capturada["url"]
    assert "timezone=UTC" in capturada["url"]
    assert "wind_gusts_10m" in capturada["url"]


# --- rejilla -----------------------------------------------------------------


def test_grid_covers_the_required_number_of_points():
    """RF-P-09 pide 35 puntos; se usan muchos más.

    Con 41 puntos el campo interpolado era tan pobre que la capa animada
    resultaba invisible, y subir la densidad del dibujo sin subir la de los
    datos habría sido inventar detalle que no existe.
    """
    assert len(wind.GRID_POINTS) >= 150


def test_grid_includes_islands():
    """Se comprueba cobertura, no topónimos: la rejilla es regular y no cae
    sobre ciudades, así que llamar "Madrid" a un nodo sería mentir sobre dónde
    se midió."""
    canarias = [p for p in wind.GRID_POINTS if p[1] < 30 and p[2] < -13]
    baleares = [p for p in wind.GRID_POINTS if 38.5 < p[1] < 40.2 and 1.0 < p[2] < 4.4]

    assert len(canarias) >= 4, "sin nodos sobre Canarias"
    assert len(baleares) >= 2, "sin nodos sobre Baleares"


def test_grid_is_dense_enough_to_interpolate():
    """La separación entre nodos debe estar en el orden de lo que el modelo
    resuelve. Más fino sería precisión fingida; más grueso, un campo plano."""
    peninsulares = sorted({p[1] for p in wind.GRID_POINTS if p[1] > 35})
    saltos = [
        peninsulares[i + 1] - peninsulares[i] for i in range(len(peninsulares) - 1)
    ]

    assert 0.5 <= min(saltos) <= 1.0


def test_grid_points_are_inside_spain():
    for nombre, lat, lon in wind.GRID_POINTS:
        assert 27 <= lat <= 44, nombre
        assert -19 <= lon <= 5, nombre


# --- Temperatura y humedad --------------------------------------------------


def test_publica_temperatura_y_humedad():
    """Van con el viento porque llegan en la misma respuesta.

    38 ºC con 15 % de humedad y 40 km/h de viento es la combinación que propaga
    un incendio, y ninguno de los tres números por separado lo dice.
    """
    payload = [{
        "latitude": 40.0,
        "longitude": -3.0,
        "current": {
            "wind_speed_10m": 40.0,
            "wind_direction_10m": 270.0,
            "wind_gusts_10m": 65.0,
            "temperature_2m": 38.4,
            "relative_humidity_2m": 15.0,
            "time": "2026-07-29T14:00",
        },
    }]
    fila = wind.parse(payload, points=[("prueba", 40.0, -3.0)]).iloc[0]

    assert fila["temp_c"] == 38.4
    assert fila["humedad_pct"] == 15.0


def test_sin_temperatura_el_punto_sigue_siendo_valido():
    """Open-Meteo puede omitir una variable sin omitir el bloque entero.

    Descartar el punto por eso perdería el viento, que es el dato principal de
    esta capa. Se publica nulo, que el frontend sabe no pintar.
    """
    payload = [{
        "latitude": 40.0,
        "longitude": -3.0,
        "current": {
            "wind_speed_10m": 20.0,
            "wind_direction_10m": 180.0,
            "time": "2026-07-29T14:00",
        },
    }]
    fila = wind.parse(payload, points=[("prueba", 40.0, -3.0)]).iloc[0]

    assert fila["direction_from_deg"] == 180.0
    assert pd.isna(fila["temp_c"])
