"""Pruebas del 112 · Comunitat Valenciana.

El fixture es real, capturado el 31-07-2026: cuatro incendios de vegetación de
ramas distintas, una incidencia que no es incendio y uno sin coordenadas.

La mayoría de estas pruebas existen por **una** característica de la fuente: es
un feed de **incidencias del 112**, no de incendios. De 58 registros del día,
15 eran incendios. Filtrar mal publicaría un accidente de tráfico como incendio
forestal, y en un visor que la gente mira asustada eso es de lo peor que puede
pasar.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from incendios.sources import cv112

FIXTURE = Path(__file__).parent / "fixtures" / "112cv.json"


@pytest.fixture
def payload() -> list:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


# --- El filtro: qué es y qué no es un incendio ------------------------------


def test_solo_se_publican_incendios_de_vegetacion(payload):
    """El feed trae accidentes, contaminación, salvamentos y suministros."""
    filas = cv112.extraer(payload)

    assert all("incendio" in f["raw_status"].lower() for f in filas)


def test_una_incidencia_que_no_es_incendio_se_descarta(payload):
    """«Medioambiente > Contaminación > Mar» no es un incendio forestal."""
    ids = {f["external_id"] for f in cv112.extraer(payload)}

    assert "40137158" not in ids


def test_el_incendio_urbano_se_excluye(payload):
    """Un incendio de vegetación urbana —un solar, una mediana— no es lo que
    este visor cubre, y mezclarlo inflaría el recuento con sucesos que no
    preocupan a quien mira si arde el monte."""
    filas = cv112.extraer(payload)

    assert all("urbana" not in f["raw_status"].lower() for f in filas)


@pytest.mark.parametrize("descripcion,esperado", [
    ("Incendio > Vegetación > Forestal", True),
    ("Incendio > Vegetación > Rural/Montañosa", True),
    ("Incendio > Vegetación > Rural/Montañosa Humo", True),
    ("Incendio > Vegetación > Urbana", False),
    ("Incendio > Edificio > Vivienda", False),
    ("Accidente > Tráfico", False),
    ("Medioambiente > Contaminación > Mar", False),
    ("", False),
])
def test_la_taxonomia_del_112_se_interpreta_bien(descripcion, esperado):
    assert cv112._es_incendio_publicable(descripcion) is esperado


# --- Humo frente a llama ----------------------------------------------------


def test_el_aviso_de_humo_se_marca_aparte(payload):
    """La fuente distingue «Rural/Montañosa» de «Rural/Montañosa Humo».

    No es lo mismo llama confirmada que alguien que ha visto humo. Se publica
    porque un aviso de humo en monte es justo lo que hay que enseñar pronto,
    pero la ficha tiene que poder decir cuál de las dos cosas es.
    """
    por_id = {f["external_id"]: f for f in cv112.extraer(payload)}

    assert por_id["40229899"]["solo_humo"] is True
    assert por_id["39971604"]["solo_humo"] is False


# --- Coordenadas ------------------------------------------------------------


def test_las_coordenadas_caen_en_la_comunitat(payload):
    for fila in cv112.extraer(payload):
        assert 37.8 < fila["latitude"] < 40.9, f"latitud fuera: {fila['latitude']}"
        assert -1.6 < fila["longitude"] < 0.8, f"longitud fuera: {fila['longitude']}"


def test_un_incidente_sin_coordenadas_se_descarta(payload):
    """Un incendio sin posición no se puede pintar y no se publica: la lista
    diría que hay uno más y el mapa no lo enseñaría en ninguna parte."""
    ids = {f["external_id"] for f in cv112.extraer(payload)}

    assert "99999999" not in ids


def test_se_toma_la_primera_coordenada_no_el_promedio(payload):
    """Un incidente puede traer varias localizaciones —dos frentes, dos extremos
    de un corte—. El punto medio entre dos frentes puede caer donde no arde
    nada, así que se coge la primera y no se promedia."""
    incidente = dict(payload[0])
    incidente["coordenadas"] = [{"x": 0.3725, "y": 40.41367}, {"x": -1.0, "y": 38.0}]

    fila = cv112.extraer([incidente])[0]

    assert fila["longitude"] == pytest.approx(0.3725)


def test_una_coordenada_fuera_del_recuadro_se_descarta(payload):
    """Aquí las coordenadas ya llegan en grados, pero un cambio de formato en
    origen se manifestaría como un punto plausible en otro sitio."""
    incidente = dict(payload[0])
    incidente["coordenadas"] = [{"x": 4468904, "y": 352454}]

    assert cv112.extraer([incidente]) == []


# --- Lo que esta fuente aporta y ninguna otra da ----------------------------


def test_publica_la_direccion_en_texto_libre(payload):
    """«AP-7 Km364 >sur» sitúa el fuego respecto a una carretera, que es como la
    gente localiza las cosas. Ninguna otra fuente da esto."""
    por_id = {f["external_id"]: f for f in cv112.extraer(payload)}

    assert por_id["39971604"]["detalle"] == "AP-7 Km364 >sur"


def test_sin_direccion_el_detalle_queda_nulo(payload):
    incidente = dict(payload[0])
    incidente["direccion"] = "   "

    assert cv112.extraer([incidente])[0]["detalle"] is None


# --- Lo que la fuente NO da -------------------------------------------------


def test_no_hay_fecha_y_no_se_inventa(payload):
    """Ningún campo del feed lleva fecha ni hora.

    `reported_at` queda nulo y `base.py` lo rellena con el instante de la
    ejecución, que es lo único defendible: el feed son las incidencias vigentes
    ahora. Inventar una hora de inicio desplazaría la ventana de emparejamiento
    de 48 h con FIRMS.
    """
    assert all(f["reported_at"] is None for f in cv112.extraer(payload))


def test_no_se_inventa_nivel_de_gravedad(payload):
    """El 112 no publica escala de gravedad. Derivarla del tipo de incendio
    sería una afirmación nuestra sobre lo grave que es un fuego real."""
    assert all(f["level"] is None for f in cv112.extraer(payload))


# --- Casos degenerados ------------------------------------------------------


def test_una_lista_vacia_no_lanza():
    assert cv112.extraer([]) == []


def test_una_respuesta_que_no_es_lista_no_lanza():
    """Si la Generalitat envuelve el feed en un objeto, el adaptador devuelve
    vacío y la fuente se marca sin datos, en vez de reventar el pipeline."""
    assert cv112.extraer({"incidentes": []}) == []


def test_un_registro_sin_descripcion_no_lanza():
    assert cv112.extraer([{"id": 1, "coordenadas": [{"x": 0.0, "y": 39.0}]}]) == []
