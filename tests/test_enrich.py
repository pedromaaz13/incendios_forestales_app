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
from shapely.geometry import Polygon

from conftest import make_fires
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
