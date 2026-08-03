"""Geocoding inverso · RF-P-07.

El módulo es opcional por diseño: sin `config/municipios.geojson` el pipeline
sigue y deja los campos a nulo. Eso es correcto —el repo tiene que arrancar sin
descargas manuales de 30 MB— pero convierte el fallo en silencioso, así que lo
que más se prueba aquí es que la degradación avise y no invente nombres.

La capa municipal de la mayoría de estos tests es sintética. La validación real
de RF-P-07 —20 coordenadas conocidas contra su municipio esperado— vive al final
del fichero y corre contra los recintos del IGN de verdad.
"""

from __future__ import annotations

import geopandas as gpd
import pandas as pd
import pytest
from conftest import make_fires
from shapely.geometry import Polygon

from incendios import enrich as enrich_mod
from incendios.config import CRS_WGS84


@pytest.fixture
def capa_municipal(tmp_path):
    """Dos municipios cuadrados y contiguos, con los nombres de columna del IGN."""
    path = tmp_path / "municipios.geojson"
    gpd.GeoDataFrame(
        {
            "NAMEUNIT": ["Burgohondo", "Navaluenga"],
            "provincia": ["Ávila", "Ávila"],
            "geometry": [
                Polygon([(-5.0, 40.0), (-4.9, 40.0), (-4.9, 40.1), (-5.0, 40.1)]),
                Polygon([(-4.9, 40.0), (-4.8, 40.0), (-4.8, 40.1), (-4.9, 40.1)]),
            ],
        },
        crs=CRS_WGS84,
    ).to_file(path, driver="GeoJSON")
    return path


def test_missing_layer_degrades_without_crashing(tmp_path, caplog):
    """Sin capa, campos a nulo y aviso. Nunca un municipio inventado."""
    fires = make_fires([{"fire_id": "f1", "latitude": 40.05, "longitude": -4.95}])

    with caplog.at_level("WARNING"):
        out = enrich_mod.enrich_admin(fires, path=tmp_path / "no-existe.geojson")

    assert out["municipio"].isna().all()
    assert out["provincia"].isna().all()
    assert len(out) == len(fires)
    assert any("municipios" in r.getMessage() for r in caplog.records)


def test_assigns_municipality_and_province(capa_municipal):
    fires = make_fires(
        [
            {"fire_id": "f1", "latitude": 40.05, "longitude": -4.95},
            {"fire_id": "f2", "latitude": 40.05, "longitude": -4.85},
        ]
    )

    out = enrich_mod.enrich_admin(fires, path=capa_municipal)

    assert out["municipio"].tolist() == ["Burgohondo", "Navaluenga"]
    assert out["provincia"].tolist() == ["Ávila", "Ávila"]


def test_point_outside_every_municipality_stays_null(capa_municipal):
    """Un incendio en el mar o fuera de la cobertura no recibe nombre."""
    fires = make_fires([{"fire_id": "f1", "latitude": 35.0, "longitude": -2.0}])

    out = enrich_mod.enrich_admin(fires, path=capa_municipal)

    assert out["municipio"].isna().all()


def test_unrecognised_name_column_degrades_with_error(tmp_path, caplog):
    """Si la capa no trae columna de nombre reconocible, se avisa y se sigue."""
    path = tmp_path / "raro.geojson"
    gpd.GeoDataFrame(
        {
            "COLUMNA_RARA": ["X"],
            "geometry": [Polygon([(-5.0, 40.0), (-4.9, 40.0), (-4.9, 40.1), (-5.0, 40.1)])],
        },
        crs=CRS_WGS84,
    ).to_file(path, driver="GeoJSON")
    fires = make_fires([{"fire_id": "f1", "latitude": 40.05, "longitude": -4.95}])

    with caplog.at_level("ERROR"):
        out = enrich_mod.enrich_admin(fires, path=path)

    assert out["municipio"].isna().all()
    assert any("nombre" in r.getMessage() for r in caplog.records)


def test_layer_without_province_column_still_assigns_municipality(tmp_path):
    path = tmp_path / "sin_provincia.geojson"
    gpd.GeoDataFrame(
        {
            "NAMEUNIT": ["Burgohondo"],
            "geometry": [Polygon([(-5.0, 40.0), (-4.9, 40.0), (-4.9, 40.1), (-5.0, 40.1)])],
        },
        crs=CRS_WGS84,
    ).to_file(path, driver="GeoJSON")
    fires = make_fires([{"fire_id": "f1", "latitude": 40.05, "longitude": -4.95}])

    out = enrich_mod.enrich_admin(fires, path=path)

    assert out["municipio"].tolist() == ["Burgohondo"]
    assert out["provincia"].isna().all()


def test_enrich_does_not_duplicate_rows_on_overlapping_polygons(tmp_path):
    """Los límites municipales se tocan: un punto en la frontera cae en dos
    polígonos y el sjoin devolvería dos filas. Debe quedarse una."""
    path = tmp_path / "solapados.geojson"
    gpd.GeoDataFrame(
        {
            "NAMEUNIT": ["A", "B"],
            "geometry": [
                Polygon([(-5.0, 40.0), (-4.9, 40.0), (-4.9, 40.1), (-5.0, 40.1)]),
                Polygon([(-5.0, 40.0), (-4.9, 40.0), (-4.9, 40.1), (-5.0, 40.1)]),
            ],
        },
        crs=CRS_WGS84,
    ).to_file(path, driver="GeoJSON")
    fires = make_fires([{"fire_id": "f1", "latitude": 40.05, "longitude": -4.95}])

    out = enrich_mod.enrich_admin(fires, path=path)

    assert len(out) == 1


# --- RF-P-07 · la validación real, contra los recintos del IGN --------------
#
# Por qué existe: `enrich_admin` publica «Dónde: X» en cada incendio, y hasta
# hoy nadie había comprobado que acierte. Es el patrón que ya nos mordió dos
# veces —la precisión de MODIS y la cuota de FIRMS—: un número plausible que
# nadie contrastó.
#
# Cómo se han elegido las coordenadas, que es lo único que hace que este test
# valga algo. **No** salen de consultar `municipios.geojson`: son lugares
# identificables y el municipio esperado se afirma con conocimiento
# independiente de la capa. Sacarlas del propio GeoJSON las haría pasar por
# construcción y no probaría nada, que es exactamente cómo la prueba de la
# cuota de FIRMS pasó durante semanas contra un campo que la fuente no manda.
#
# Los nombres son las denominaciones oficiales del IGN, en lengua cooficial
# donde corresponde: «València», «Vimbodí i Poblet», «Torla-Ordesa».

VEINTE_COORDENADAS = [
    # (lat, lon, municipio, provincia, referencia)
    (40.4169, -3.7038, "Madrid", "Madrid", "Puerta del Sol"),
    (41.4036, 2.1744, "Barcelona", "Barcelona", "Sagrada Família"),
    (37.3861, -5.9926, "Sevilla", "Sevilla", "Giralda"),
    # El IGN publica esta provincia con las dos lenguas en el mismo campo.
    (39.4545, -0.3510, "València", "València/Valencia", "Ciutat de les Arts"),
    (43.2687, -2.9340, "Bilbao", "Bizkaia", "Guggenheim"),
    (37.8790, -4.7794, "Córdoba", "Córdoba", "Mezquita"),
    (37.1761, -3.5881, "Granada", "Granada", "Alhambra"),
    (42.8806, -8.5446, "Santiago de Compostela", "A Coruña", "Catedral"),
    (40.9481, -4.1184, "Segovia", "Segovia", "Acueducto"),
    (40.6565, -4.6818, "Ávila", "Ávila", "Murallas"),
    # De aquí abajo, municipios pequeños: son los que de verdad prueban el
    # geocoding. En una capital, un error de 2 km sigue cayendo dentro.
    (41.3806, 1.0817, "Vimbodí i Poblet", "Tarragona", "Monasterio de Poblet"),
    (28.2724, -16.6425, "La Orotava", "Santa Cruz de Tenerife", "Cumbre del Teide"),
    (42.6417, -0.1108, "Torla-Ordesa", "Huesca", "Ordesa"),
    (43.3078, -5.0556, "Cangas de Onís", "Asturias", "Covadonga"),
    (36.7423, -5.1665, "Ronda", "Málaga", "Puente Nuevo"),
    (40.0765, -2.1272, "Cuenca", "Cuenca", "Casas Colgadas"),
    (43.3776, -4.1200, "Santillana del Mar", "Cantabria", "Altamira"),
    (41.1969, -1.7861, "Nuévalos", "Zaragoza", "Monasterio de Piedra"),
    (42.4589, -6.7700, "Carucedo", "León", "Las Médulas"),
    (37.1319, -6.4869, "Almonte", "Huelva", "El Rocío"),
]


@pytest.mark.skipif(
    not enrich_mod.MUNICIPIOS_PATH.exists(),
    reason=f"sin los recintos del IGN en {enrich_mod.MUNICIPIOS_PATH}",
)
@pytest.mark.parametrize(
    "lat,lon,municipio,provincia,referencia",
    VEINTE_COORDENADAS,
    ids=[c[4] for c in VEINTE_COORDENADAS],
)
def test_veinte_coordenadas_conocidas(lat, lon, municipio, provincia, referencia):
    """RF-P-07. Si esto falla, llevamos meses publicando municipios erróneos.

    Ante una discrepancia hay que averiguar quién se equivoca —la coordenada o
    el geocoding— y **no** ajustar el valor esperado para que pase.
    """
    fila = enrich_mod.enrich_admin(
        make_fires([{"fire_id": "f1", "latitude": lat, "longitude": lon}])
    ).iloc[0]

    assert fila["municipio"] == municipio, referencia
    assert fila["provincia"] == provincia, referencia


@pytest.mark.skipif(
    not enrich_mod.MUNICIPIOS_PATH.exists(),
    reason=f"sin los recintos del IGN en {enrich_mod.MUNICIPIOS_PATH}",
)
@pytest.mark.parametrize("lat,lon,donde", [
    (35.7595, -5.8340, "Tánger, Marruecos"),
    (38.7223, -9.1393, "Lisboa, Portugal"),
    (43.6047, 1.4442, "Toulouse, Francia"),
    (39.50, 1.50, "mar Mediterráneo"),
])
def test_fuera_de_espana_no_inventa_municipio(lat, lon, donde):
    """Los bbox de FIRMS arrastran país vecino y mar abierto.

    Un nulo es honesto; asignar el municipio más cercano sería afirmar que hay
    un incendio en un pueblo que no lo tiene.
    """
    fila = enrich_mod.enrich_admin(
        make_fires([{"fire_id": "f1", "latitude": lat, "longitude": lon}])
    ).iloc[0]

    assert fila["municipio"] is None or pd.isna(fila["municipio"]), donde


@pytest.mark.skipif(
    not enrich_mod.MUNICIPIOS_PATH.exists(),
    reason=f"sin los recintos del IGN en {enrich_mod.MUNICIPIOS_PATH}",
)
@pytest.mark.parametrize("lat,lon,municipio", [
    (35.8894, -5.3213, "Ceuta"),
    (35.2937, -2.9383, "Melilla"),
    (28.0916, -15.4197, "Las Palmas de Gran Canaria"),
])
def test_ciudades_autonomas_e_islas_son_espana(lat, lon, municipio):
    """Ceuta y Melilla caen en pleno bbox del norte de África: el recorte a
    España tiene que dejarlas dentro, no confundirlas con Marruecos."""
    fila = enrich_mod.enrich_admin(
        make_fires([{"fire_id": "f1", "latitude": lat, "longitude": lon}])
    ).iloc[0]

    assert fila["municipio"] == municipio


# --- recorte a España · los bbox de FIRMS arrastran país vecino --------------


def _capa_espana(tmp_path):
    """Un recinto que hace de España: rectángulo peninsular aproximado."""
    from shapely.geometry import box

    path = tmp_path / "municipios.geojson"
    gpd.GeoDataFrame(
        {"NAMEUNIT": ["Interior"], "geometry": [box(-7.0, 37.0, 3.0, 43.0)]},
        crs=CRS_WGS84,
    ).to_file(path, driver="GeoJSON")
    return path


def _puntos(coords):
    return gpd.GeoDataFrame(
        {"id": list(range(len(coords)))},
        geometry=gpd.points_from_xy([c[1] for c in coords], [c[0] for c in coords]),
        crs=CRS_WGS84,
    )


def test_discards_fires_clearly_outside_spain(tmp_path):
    """El bbox de la península cubre Portugal entero y parte de Argelia.

    Sin recortar, la mitad de los incendios "de España" que publicaba la
    aplicación eran portugueses o argelinos, y eso infla el recuento de un
    visor cuyo título dice España.
    """
    capa = _capa_espana(tmp_path)
    gdf = _puntos([
        (40.0, -5.0),    # interior peninsular
        (36.6, 3.1),     # Argelia, a cientos de km
        (39.9, -8.8),    # Portugal, a más de 100 km de la raya
    ])

    dentro, fuera = enrich_mod.clip_to_spain(gdf, path=capa)

    assert fuera == 2
    assert dentro["id"].tolist() == [0]


def test_keeps_a_fire_just_across_the_border(tmp_path):
    """Un fuego a pocos kilómetros de la raya le sigue importando a quien vive
    en Zamora o en Badajoz: no se descarta por estar del otro lado."""
    capa = _capa_espana(tmp_path)
    # ~8 km al oeste del borde del recinto.
    gdf = _puntos([(40.0, -7.09)])

    dentro, fuera = enrich_mod.clip_to_spain(gdf, path=capa)

    assert fuera == 0
    assert len(dentro) == 1


def test_keeps_a_coastal_fire_slightly_offshore(tmp_path):
    """La geolocalización de VIIRS tiene un error de un par de kilómetros y un
    incendio costero puede caer mar adentro. Descartarlo perdería fuegos
    españoles reales."""
    capa = _capa_espana(tmp_path)
    # ~11 km al sur del borde. La distancia se mide en UTM 30N, donde el borde
    # del recinto en lat 37 no es una recta: por eso 0,01° no son 1,1 km
    # exactos y conviene fijar el caso con una distancia comprobada.
    gdf = _puntos([(36.99, -4.0)])

    dentro, fuera = enrich_mod.clip_to_spain(gdf, path=capa)

    assert fuera == 0
    assert len(dentro) == 1


def test_margin_is_configurable(tmp_path):
    capa = _capa_espana(tmp_path)
    gdf = _puntos([(36.99, -4.0)])  # ~11 km fuera

    assert len(enrich_mod.clip_to_spain(gdf, path=capa, margen_m=20_000)[0]) == 1
    assert len(enrich_mod.clip_to_spain(gdf, path=capa, margen_m=1_000)[0]) == 0


def test_no_layer_means_no_clipping(tmp_path):
    """Sin capa municipal es preferible publicar de más —y avisarlo en el log—
    que descartar incendios reales por un fichero que no está."""
    gdf = _puntos([(40.0, -5.0), (36.6, 3.1)])

    dentro, fuera = enrich_mod.clip_to_spain(gdf, path=tmp_path / "no-existe.geojson")

    assert fuera == 0
    assert len(dentro) == 2


def test_empty_input_is_handled(tmp_path):
    capa = _capa_espana(tmp_path)
    vacio = _puntos([(40.0, -5.0)]).iloc[0:0]

    dentro, fuera = enrich_mod.clip_to_spain(vacio, path=capa)

    assert len(dentro) == 0
    assert fuera == 0
