"""Geocoding inverso · RF-P-07.

El módulo es opcional por diseño: sin `config/municipios.geojson` el pipeline
sigue y deja los campos a nulo. Eso es correcto —el repo tiene que arrancar sin
descargas manuales de 30 MB— pero convierte el fallo en silencioso, así que lo
que más se prueba aquí es que la degradación avise y no invente nombres.

La capa municipal de estos tests es sintética. La validación real de RF-P-07
(20 coordenadas conocidas con su municipio esperado) necesita los recintos del
IGN y queda pendiente.
"""

from __future__ import annotations

import geopandas as gpd
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


@pytest.mark.skip(
    reason=(
        "RF-P-07 exige 20 coordenadas conocidas contra su municipio esperado. "
        "Necesita los recintos municipales del IGN en config/municipios.geojson "
        "(~30 MB, descarga manual). Pendiente de que la capa esté en el repo o "
        "en el cacheado de CI."
    )
)
def test_twenty_known_coordinates_against_ign():
    raise AssertionError("pendiente de config/municipios.geojson (IGN)")


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
