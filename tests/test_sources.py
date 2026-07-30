"""Framework de fuentes oficiales · RF-P-03, tabla 8.1 (filas 4-7).

Se prueba **el framework**, no ninguna comunidad concreta: los endpoints siguen
sin descubrir (`docs/COMO-CONECTAR-LAS-FUENTES.md`) y no se inventan. El fixture
`arcgis_framework_sample.json` es sintético y está marcado como tal; los
fixtures de regresión reales que exige RF-P-03 son `tests/fixtures/{source_id}.json`
y solo pueden crearse con una respuesta real de cada portal.

La URL de prueba usa el TLD reservado `.invalid` (RFC 2606) para que no pueda
confundirse con un endpoint real ni resolver por accidente.
"""

from __future__ import annotations

import httpx
import pandas as pd
import pytest
from conftest import load_json_fixture

from incendios.sources.adapters import COMMON_STATUS_MAP, ArcGISSource, JsonApiSource
from incendios.sources.base import (
    OFFICIAL_SCHEMA,
    STATUS_UNKNOWN,
    OfficialSource,
    SourceMeta,
)

URL_PRUEBA = "https://fuente-de-prueba.invalid/FeatureServer/0/query"

FIELD_MAP = {
    "external_id": "OBJECTID",
    "status": "ESTADO",
    "municipio": "MUNICIPIO",
    "provincia": "PROVINCIA",
    "level": "NIVEL_IGR",
    "resources": "MEDIOS",
    "reported_at": "FECHA_ALTA",
}


def _meta(**kwargs) -> SourceMeta:
    base = {
        "source_id": "prueba",
        "name": "Fuente de prueba",
        "region": "Region de prueba",
        "url": URL_PRUEBA,
        "precision_m": 1500.0,
        "ttl_seconds": 300,
    }
    return SourceMeta(**{**base, **kwargs})


def _source(**kwargs) -> ArcGISSource:
    return ArcGISSource(meta=_meta(**kwargs.pop("meta", {})), field_map=FIELD_MAP, **kwargs)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _json_response(payload, status: int = 200):
    return lambda request: httpx.Response(status, json=payload)


# --- tabla 8.1: la fuente devuelve 500 --------------------------------------


def test_http_500_returns_empty_without_raising(caplog):
    """RF-P-03: en agosto la web de alguna comunidad SIEMPRE está caída.

    El `try` de `OfficialSource.collect` existe justo para esto y la
    especificación prohíbe quitarlo.
    """
    with (
        caplog.at_level("ERROR"),
        _client(_json_response({"error": "boom"}, status=500)) as client,
    ):
        df = _source().collect(client)

    assert df.empty
    assert list(df.columns) == OFFICIAL_SCHEMA
    assert any("prueba" in r.getMessage() for r in caplog.records)


def test_timeout_returns_empty_without_raising():
    def handler(request):
        raise httpx.ConnectTimeout("timeout simulado")

    with _client(handler) as client:
        assert _source().collect(client).empty


def test_malformed_json_returns_empty_without_raising():
    handler = lambda request: httpx.Response(200, text="<html>error</html>")

    with _client(handler) as client:
        assert _source().collect(client).empty


def test_one_broken_source_does_not_stop_the_others():
    """El aislamiento por fuente es lo que hace publicable el resto del mapa."""
    caida = _source(meta={"source_id": "caida"})
    viva = _source(meta={"source_id": "viva"})

    with _client(_json_response({"error": "boom"}, status=500)) as client:
        rota = caida.collect(client)
    with _client(_json_response(load_json_fixture("arcgis_framework_sample.json"))) as client:
        buena = viva.collect(client)

    combinado = pd.concat([rota, buena], ignore_index=True)
    assert rota.empty
    assert len(buena) > 0
    assert len(combinado) == len(buena)


def test_empty_feature_collection_is_not_an_error():
    with _client(_json_response({"type": "FeatureCollection", "features": []})) as client:
        df = _source().collect(client)

    assert df.empty
    assert list(df.columns) == OFFICIAL_SCHEMA


# --- tabla 8.1: coordenadas fuera de España ---------------------------------


def test_coordinates_outside_spain_are_discarded(caplog):
    """El fixture lleva un punto en París a propósito.

    Un error de proyección o un campo cambiado de sitio produce coordenadas
    plausibles pero equivocadas; el filtro por bbox las corta antes del mapa.
    """
    payload = load_json_fixture("arcgis_framework_sample.json")

    with caplog.at_level("WARNING"), _client(_json_response(payload)) as client:
        df = _source().collect(client)

    assert len(df) == 3  # 4 features, una fuera de España
    assert df["latitude"].between(27, 44).all()
    assert df["longitude"].between(-19, 5).all()
    assert any("fuera de España" in r.getMessage() for r in caplog.records)


# --- tabla 8.1: estado desconocido en un parte ------------------------------


def test_unknown_status_falls_back_and_keeps_raw():
    """'En vigilancia posterior' no está en el vocabulario: `desconocido`.

    `raw_status` se conserva sin normalizar: es lo que permite descubrir que una
    comunidad ha cambiado su vocabulario sin tener que releer su web.
    """
    payload = load_json_fixture("arcgis_framework_sample.json")

    with _client(_json_response(payload)) as client:
        df = _source().collect(client)

    fila = df[df["municipio"] == "Puertollano"].iloc[0]
    assert fila["status"] == STATUS_UNKNOWN
    assert fila["raw_status"] == "En vigilancia posterior"


@pytest.mark.parametrize(
    ("crudo", "esperado"),
    [
        ("Nivel 1 - Activo", "activo"),
        ("ACTIVO", "activo"),
        ("En curso", "activo"),
        ("Declarado", "activo"),
        ("Estabilizado", "estabilizado"),
        ("Controlado", "controlado"),
        ("Extinguido", "extinguido"),
        ("Incendio extinguido a las 14:00", "extinguido"),
        ("", STATUS_UNKNOWN),
        (None, STATUS_UNKNOWN),
        ("Fase de vigilancia", STATUS_UNKNOWN),
    ],
)
def test_status_normalisation_by_substring(crudo, esperado):
    """Se busca subcadena, no igualdad: las fuentes escriben cada parte distinto."""
    assert OfficialSource.norm_status(crudo, COMMON_STATUS_MAP) == esperado


def test_status_normalisation_prefers_extinguished_over_active():
    """El orden del mapa importa: 'incendio activo ya extinguido' no es activo."""
    assert OfficialSource.norm_status("Extinguido", COMMON_STATUS_MAP) == "extinguido"


# --- tabla 8.1: la fuente cambia el nombre de un campo ----------------------


def test_renamed_field_warns_instead_of_silent_nulls(caplog):
    """Es el modo de fallo del riesgo 1 de la sección 11: formato cambiado sin aviso."""
    payload = load_json_fixture("arcgis_framework_sample.json")
    fuente = ArcGISSource(
        meta=_meta(),
        field_map={**FIELD_MAP, "status": "ESTADO_RENOMBRADO", "municipio": "MUNI_V2"},
    )

    with caplog.at_level("WARNING"), _client(_json_response(payload)) as client:
        df = fuente.collect(client)

    assert df["municipio"].isna().all()
    assert any(
        "ESTADO_RENOMBRADO" in r.getMessage() or "campo" in r.getMessage().lower()
        for r in caplog.records
    )


def test_renamed_field_still_yields_nulls_but_no_longer_silently():
    """El aviso no rellena los datos: la fuente sigue devolviendo nulos.

    Arreglarlo de verdad es descubrir el nombre nuevo y actualizar el
    `field_map`. Lo que cambia es que ahora se sabe que hay que hacerlo.
    """
    payload = load_json_fixture("arcgis_framework_sample.json")
    fuente = ArcGISSource(
        meta=_meta(),
        field_map={**FIELD_MAP, "status": "ESTADO_RENOMBRADO", "municipio": "MUNI_V2"},
    )

    with _client(_json_response(payload)) as client:
        df = fuente.collect(client)

    assert df["municipio"].isna().all()
    assert (df["status"] == STATUS_UNKNOWN).all()


# --- contrato de salida ------------------------------------------------------


def test_output_matches_the_official_schema():
    payload = load_json_fixture("arcgis_framework_sample.json")

    with _client(_json_response(payload)) as client:
        df = _source().collect(client)

    assert list(df.columns) == OFFICIAL_SCHEMA
    assert (df["source_id"] == "prueba").all()


def test_precision_defaults_to_the_source_declaration():
    """`precision_m` gobierna la tolerancia de fusión (RF-P-06) y el anillo de
    incertidumbre del mapa (RF-F-03). No puede quedar a nulo."""
    payload = load_json_fixture("arcgis_framework_sample.json")

    with _client(_json_response(payload)) as client:
        df = _source(meta={"precision_m": 6000.0}).collect(client)

    assert (df["precision_m"] == 6000.0).all()


def test_reported_at_is_parsed_as_utc():
    payload = load_json_fixture("arcgis_framework_sample.json")

    with _client(_json_response(payload)) as client:
        df = _source().collect(client)

    assert str(df["reported_at"].dt.tz) == "UTC"
    assert df["reported_at"].notna().all()


def test_missing_reported_at_falls_back_to_now():
    """Sin fecha, se asume 'ahora': un parte sin hora sigue siendo un parte."""
    payload = load_json_fixture("arcgis_framework_sample.json")
    for feat in payload["features"]:
        feat["properties"].pop("FECHA_ALTA", None)

    with _client(_json_response(payload)) as client:
        df = _source().collect(client)

    assert df["reported_at"].notna().all()


def test_arcgis_xy_geometry_is_supported():
    """ArcGIS sirve `esriGeometryPoint` con x/y en vez de `coordinates`."""
    payload = load_json_fixture("arcgis_framework_sample.json")

    with _client(_json_response(payload)) as client:
        df = _source().collect(client)

    assert "Puertollano" in df["municipio"].tolist()


def test_features_without_geometry_are_skipped():
    payload = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {}, "properties": {"OBJECTID": 1, "ESTADO": "Activo"}},
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-6.0, 40.0]},
                "properties": {"OBJECTID": 2, "ESTADO": "Activo"},
            },
        ],
    }

    with _client(_json_response(payload)) as client:
        df = _source().collect(client)

    assert len(df) == 1
    assert df["external_id"].tolist() == [2]


# --- JsonApiSource -----------------------------------------------------------


def test_json_api_source_uses_its_extractor():
    """Añadir una comunidad con JSON propio debe ser escribir una lambda."""
    payload = {"incidencias": [{"id": 7, "lat": 39.5, "lon": -0.4, "estado": "Activo"}]}

    fuente = JsonApiSource(
        meta=_meta(source_id="json", precision_m=100.0),
        extract=lambda raw: [
            {
                "external_id": i["id"],
                "latitude": i["lat"],
                "longitude": i["lon"],
                "status": i["estado"],
            }
            for i in raw["incidencias"]
        ],
    )

    with _client(_json_response(payload)) as client:
        df = fuente.collect(client)

    assert len(df) == 1
    assert df["status"].iloc[0] == "activo"
    assert df["raw_status"].iloc[0] == "Activo"
    assert df["precision_m"].iloc[0] == 100.0


def test_json_api_source_with_empty_extraction():
    fuente = JsonApiSource(meta=_meta(source_id="json"), extract=lambda raw: [])

    with _client(_json_response({"incidencias": []})) as client:
        assert fuente.collect(client).empty


# --- registro ----------------------------------------------------------------


def test_registry_sources_are_disabled_until_configured():
    """Regla dura de CLAUDE.md: los adaptadores tienen la URL vacía a propósito.

    JCyL ya no está en la lista: su endpoint se descubrió el 30-07-2026 con
    DevTools sobre INFORCYL y está verificado contra un fixture real. Las otras
    cuatro siguen vacías, y lo estarán hasta que alguien repita el proceso.

    Una URL inventada devuelve 404 en silencio y eso se lee como 'hoy no hay
    incendios'. Este test se pondrá rojo el día que alguien pegue un endpoint
    sin actualizar la documentación de descubrimiento, que es justo cuando hay
    que revisar el `field_map`.
    """
    from incendios.sources.adapters import REGISTRY

    sin_configurar = [s.meta.source_id for s in REGISTRY if not s.meta.url]
    assert sorted(sin_configurar) == ["112cv", "bombers", "infoca", "infocam"]


def test_collect_all_skips_unconfigured_sources(monkeypatch):
    """Las fuentes sin URL se omiten sin intentar la petición.

    Se sustituye el registro por las cuatro que siguen sin endpoint. Antes este
    test llamaba al registro real, y en cuanto JCyL tuvo URL **empezó a hacer una
    petición de verdad a servicios.jcyl.es** desde la suite. La suite corre sin
    red por diseño: toda fuente externa tiene su fixture.
    """
    from incendios.sources import adapters

    sin_url = [s for s in adapters.REGISTRY if not s.meta.url]
    assert sin_url, "el test pierde sentido si todas las fuentes están configuradas"
    monkeypatch.setattr(adapters, "REGISTRY", sin_url)

    df = adapters.collect_all(only_configured=True)

    assert df.empty
    assert list(df.columns) == OFFICIAL_SCHEMA


def test_el_registro_cubre_las_cinco_fuentes_de_rf_p_03():
    """Las cinco de RF-P-03 tienen adaptador, aunque ninguna tenga URL todavía.

    Regresión de un desajuste entre código y documentación: la guía de conexión
    listaba cinco comunidades y el registro solo tenía tres. Si alguien
    conseguía la URL de INFOCA no había dónde meterla, y el recuento de "cinco
    endpoints pendientes" que se repetía en los informes era falso.
    """
    from incendios.sources.adapters import REGISTRY

    assert sorted(s.meta.source_id for s in REGISTRY) == [
        "112cv", "bombers", "infoca", "infocam", "jcyl",
    ]


def test_cada_fuente_declara_precision_atribucion_y_ttl():
    """Los tres campos que gobiernan el comportamiento aguas abajo.

    `precision_m` fija la tolerancia de la fusión y el radio del anillo de
    incertidumbre; `attribution` es lo mínimo para que no nos corten el acceso;
    `ttl_seconds` evita machacar un servicio público.
    """
    from incendios.sources.adapters import REGISTRY

    for fuente in REGISTRY:
        meta = fuente.meta
        assert meta.precision_m > 0, f"{meta.source_id} sin precisión declarada"
        assert meta.attribution, f"{meta.source_id} sin atribución"
        assert meta.ttl_seconds >= 300, f"{meta.source_id} pide con demasiada frecuencia"
