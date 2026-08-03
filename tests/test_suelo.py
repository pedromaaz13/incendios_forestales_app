"""Pruebas del uso del suelo · CORINE Land Cover 2018.

Lo que este módulo decide es si un «incendio» está sobre monte o sobre cultivo,
y esa distinción cambia cómo hay que leer el mapa: una quema de rastrojo en julio
no es un incendio forestal.

Las respuestas se simulan con `httpx.MockTransport` contra el esquema real del
servicio de la EEA, verificado el 03-08-2026 en cuatro puntos de España.
"""

from __future__ import annotations

import geopandas as gpd
import httpx
import pytest
from shapely.geometry import Point

from incendios import suelo
from incendios.config import CRS_WGS84


def _incendios(*puntos: tuple[float, float]) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"id": [f"f{i}" for i in range(len(puntos))]},
        geometry=[Point(lon, lat) for lat, lon in puntos],
        crs=CRS_WGS84,
    )


def _cliente(codigo: str | None = "324", *, error: str | None = None, vacio: bool = False):
    """Respuesta con la forma real del servicio de la EEA."""
    def handler(request):
        if error:
            return httpx.Response(200, json={"error": {"message": error}})
        if vacio:
            return httpx.Response(200, json={"features": []})
        return httpx.Response(200, json={
            "features": [{"attributes": {"OBJECTID": 1, "Code_18": codigo}}]
        })

    return httpx.Client(transport=httpx.MockTransport(handler))


# --- La distinción que justifica el módulo ----------------------------------


@pytest.mark.parametrize("codigo,clase,tipo", [
    ("324", "Matorral boscoso de transición", "forestal"),
    ("312", "Bosque de coníferas", "forestal"),
    ("323", "Matorral esclerófilo", "forestal"),
    ("242", "Mosaico de cultivos", "agrícola"),
    ("223", "Olivar", "agrícola"),
    ("211", "Cultivo herbáceo de secano", "agrícola"),
])
def test_distingue_monte_de_cultivo(codigo, clase, tipo):
    """Un «incendio» sobre cultivo en julio es casi siempre rastrojo.

    Publicarlo igual que uno en monte infla el recuento y entierra los que
    importan entre los que no.
    """
    fila = suelo.anadir_uso_del_suelo(_incendios((40.0, -3.0)), _cliente(codigo)).iloc[0]

    assert fila["suelo_clase"] == clase
    assert fila["suelo_tipo"] == tipo


def test_un_codigo_desconocido_se_degrada_al_nivel_1():
    """CORINE puede añadir clases. Degradar al primer dígito sigue diciendo lo
    único que de verdad hace falta: si es monte o es cultivo."""
    fila = suelo.anadir_uso_del_suelo(_incendios((40.0, -3.0)), _cliente("399")).iloc[0]

    assert fila["suelo_tipo"] == "forestal"
    assert fila["suelo_clase"] == "Superficie forestal o seminatural"


def test_el_urbano_se_reconoce_como_tal():
    """111 es tejido urbano continuo. Es lo que sale en el centro de Madrid, y
    un foco ahí casi seguro es una falsa detección o una antorcha industrial."""
    fila = suelo.anadir_uso_del_suelo(_incendios((40.42, -3.70)), _cliente("111")).iloc[0]

    assert fila["suelo_tipo"] == "urbano"


# --- Lo que NO hace ---------------------------------------------------------


def test_no_descarta_ningun_incendio():
    """La tentación es filtrar los agrícolas y quitarlos del mapa, y sería un
    error: una quema de rastrojo que se descontrola es exactamente cómo empiezan
    muchos incendios forestales. Se etiqueta, no se esconde.
    """
    inc = _incendios((40.0, -3.0), (39.0, -2.0), (38.0, -1.0))

    salida = suelo.anadir_uso_del_suelo(inc, _cliente("242"))

    assert len(salida) == 3, "el uso del suelo no filtra, solo etiqueta"


# --- Fallos: el contexto no puede tumbar la publicación ---------------------


def test_un_error_del_servicio_deja_el_campo_nulo_y_sigue():
    fila = suelo.anadir_uso_del_suelo(
        _incendios((40.0, -3.0)), _cliente(error="Invalid geometry")
    ).iloc[0]

    assert fila["suelo_tipo"] is None
    assert fila["id"] == "f0", "el incendio se conserva"


def test_un_punto_sin_cobertura_no_lanza():
    """CORINE cubre Europa: un incendio en el mar o fuera del ámbito devuelve
    cero features."""
    fila = suelo.anadir_uso_del_suelo(_incendios((40.0, -3.0)), _cliente(vacio=True)).iloc[0]

    assert fila["suelo_codigo"] is None


def test_una_caida_de_red_no_tumba_la_ejecucion():
    def handler(request):
        raise httpx.ConnectError("Network is unreachable")

    salida = suelo.anadir_uso_del_suelo(
        _incendios((40.0, -3.0)), httpx.Client(transport=httpx.MockTransport(handler))
    )

    assert len(salida) == 1
    assert salida.iloc[0]["suelo_tipo"] is None


def test_sin_incendios_no_se_consulta_nada():
    vacio = gpd.GeoDataFrame({"id": []}, geometry=[], crs=CRS_WGS84)

    salida = suelo.anadir_uso_del_suelo(vacio, _cliente())

    assert salida.empty
    for campo in suelo.CAMPOS_SUELO:
        assert campo in salida.columns


def test_las_columnas_existen_aunque_todo_falle():
    """El frontend lee estos nombres: una columna ausente y una nula no se
    comportan igual en GeoJSON."""
    salida = suelo.anadir_uso_del_suelo(
        _incendios((40.0, -3.0)), _cliente(error="boom")
    )

    for campo in suelo.CAMPOS_SUELO:
        assert campo in salida.columns
