"""Pipeline completo sin red · sección 8.3.

Ejecuta `pipeline.run()` de punta a punta con FIRMS simulado por fixture y
verifica que los artefactos se generan, validan y cumplen el contrato. Debe
correr en menos de 30 s para que se pueda lanzar en cada commit.

Es la prueba que atrapa los fallos de integración: cada módulo puede estar bien
y aun así el conjunto publicar un manifiesto que apunta a un fichero que nadie
escribió. Aquí se comprueba el conjunto.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest
from conftest import make_hotspots

from incendios import pipeline
from incendios.config import Outputs


@pytest.fixture
def salidas(tmp_path, monkeypatch):
    """Todas las escrituras a un directorio temporal, y sin tippecanoe."""
    from incendios import export as export_mod

    out = tmp_path / "out"
    history = tmp_path / "history"
    out.mkdir()
    history.mkdir()

    salidas = Outputs(
        hotspots_geojson=out / "hotspots.geojson",
        fires_geojson=out / "fires.geojson",
        perimeters_geojson=out / "perimeters.geojson",
        hotspots_pmtiles=out / "hotspots.pmtiles",
        manifest=out / "manifest.json",
        incidents_geojson=out / "incidents.geojson",
        sources_json=out / "sources.json",
        wind_geojson=out / "wind.geojson",
        runs_json=history / "runs.json",
        history_dir=history,
    )
    monkeypatch.setattr(export_mod, "HISTORY", history)
    # tippecanoe no está en el entorno de test y su ausencia no debe fallar.
    monkeypatch.setattr(export_mod.shutil, "which", lambda _: None)
    return salidas


@pytest.fixture
def firms_simulado(monkeypatch):
    """Sustituye la ingesta por hotspots sintéticos, sin tocar la red.

    Las marcas de tiempo se desplazan al instante real de ejecución. El `NOW`
    congelado de `conftest` sirve para probar funciones puras, pero el pipeline
    compara contra `datetime.now()`: con fechas fijas, el fixture envejecía solo
    y al pasar de las 24 h la regla de "cluster satelital sin detecciones
    recientes no se publica" lo filtraba entero. La prueba pasaba el día que se
    escribió y fallaba al siguiente.
    """
    entrada = pd.concat(
        [
            make_hotspots(40.25, -6.60, n=40, spread_deg=0.008, frp=30.0, hours_ago=[2, 3]),
            make_hotspots(42.40, -7.85, n=6, spread_deg=0.003, frp=9.0, hours_ago=4),
            make_hotspots(38.28, -2.64, n=12, spread_deg=0.005, frp=20.0, hours_ago=[5, 6]),
            # Antorcha de Puertollano: la máscara industrial debe suprimirla.
            make_hotspots(38.703, -4.092, n=5, frp=25.0, hours_ago=1),
            # Confianza baja: cae en el filtro.
            make_hotspots(39.10, -2.40, n=8, spread_deg=0.01, frp=3.0,
                          confidence_raw="l", confidence_pct=20.0),
        ],
        ignore_index=True,
    )

    from conftest import NOW as CONGELADO

    desplazamiento = pd.Timestamp.now(tz="UTC").floor("min") - CONGELADO
    entrada["acq_dt"] = entrada["acq_dt"] + desplazamiento

    monkeypatch.setattr(pipeline.firms, "fetch_hotspots", lambda **_: entrada)
    return entrada


@pytest.fixture(autouse=True)
def _sin_fuentes_por_red(monkeypatch):
    """Deja en el registro solo las fuentes que aún no tienen endpoint.

    Desde que JCyL tiene URL (30-07-2026), ejecutar el pipeline aquí haría una
    petición real a `servicios.jcyl.es`. La suite corre **sin red** por diseño:
    toda fuente externa tiene su fixture, y el de JCyL se ejercita en
    `test_jcyl.py` contra la respuesta real capturada.

    `autouse` a propósito: cualquier prueba nueva de este módulo que llame a
    `pipeline.run` hereda el aislamiento sin tener que acordarse.
    """
    from incendios.sources import adapters

    monkeypatch.setattr(
        adapters, "REGISTRY", [s for s in adapters.REGISTRY if not s.meta.url]
    )


def _ejecutar(salidas) -> dict:
    # Todas las capas de contexto salen por red y esta prueba no la toca.
    return pipeline.run(
        persist_raw=False,
        con_viento=False,
        con_aire=False,
        con_trafico=False,
        outputs=salidas,
    )


# --- artefactos --------------------------------------------------------------


def test_pipeline_generates_every_artifact(firms_simulado, salidas):
    _ejecutar(salidas)

    assert salidas.incidents_geojson.exists()
    assert salidas.hotspots_geojson.exists()
    assert salidas.perimeters_geojson.exists()
    assert salidas.sources_json.exists()
    assert salidas.manifest.exists()


def test_artifacts_are_within_the_size_budget(firms_simulado, salidas):
    """Contrato 3.1. Con datos reales el volumen es mayor, pero un salto de
    orden de magnitud aquí delataría que se está publicando de más."""
    _ejecutar(salidas)

    assert salidas.manifest.stat().st_size < 4 * 1024
    assert salidas.sources_json.stat().st_size < 8 * 1024
    assert salidas.incidents_geojson.stat().st_size < 400 * 1024


def test_incidents_match_the_4_3_contract(firms_simulado, salidas):
    from incendios.build import INCIDENT_WEB_FIELDS

    _ejecutar(salidas)
    datos = json.loads(salidas.incidents_geojson.read_text(encoding="utf-8"))

    assert datos["type"] == "FeatureCollection"
    assert len(datos["features"]) > 0
    props = set(datos["features"][0]["properties"])
    assert props == set(INCIDENT_WEB_FIELDS)


def test_published_incidents_satisfy_every_invariant(firms_simulado, salidas):
    """La validación corre dentro del pipeline, pero se repite sobre el fichero
    ya escrito: entre validar y serializar puede perderse un campo."""
    import geopandas as gpd

    from incendios import validate

    _ejecutar(salidas)
    publicado = gpd.read_file(salidas.incidents_geojson)

    assert validate.check(publicado) == []


# --- las dos latencias -------------------------------------------------------


def test_manifest_publishes_both_latencies(firms_simulado, salidas):
    manifest = _ejecutar(salidas)

    assert manifest["pipeline_age_seconds"] >= 0
    assert manifest["data_age_seconds"]["firms_viirs"] > 0
    assert manifest["worst_data_age_seconds"] is not None


def test_manifest_counts_suppressed_hotspots(firms_simulado, salidas):
    """Riesgo 3 de la sección 11: lo suprimido se registra siempre."""
    manifest = _ejecutar(salidas)

    assert manifest["counts"]["hotspots_suppressed_lowconf"] == 8
    assert manifest["counts"]["hotspots_suppressed_industrial"] > 0


def test_industrial_mask_removes_the_refinery(firms_simulado, salidas):
    import geopandas as gpd

    _ejecutar(salidas)
    hotspots = gpd.read_file(salidas.hotspots_geojson)

    cerca = (
        (hotspots.geometry.y.sub(38.703).abs() < 0.02)
        & (hotspots.geometry.x.sub(-4.092).abs() < 0.02)
    ).sum()
    assert cerca == 0


# --- estado de fuentes -------------------------------------------------------


def test_sources_report_marks_undiscovered_endpoints_as_disabled(firms_simulado, salidas):
    """Una fuente sin endpoint sale `disabled`, nunca como "0 incendios".

    Es la regla dura: un hueco explícito es honesto; un cero silencioso se lee
    como «hoy no arde nada en esa comunidad».
    """
    _ejecutar(salidas)
    salud = json.loads(salidas.sources_json.read_text(encoding="utf-8"))

    por_id = {s["id"]: s for s in salud["sources"]}
    assert por_id["infocam"]["status"] == "disabled"
    assert por_id["112cv"]["status"] == "disabled"
    assert por_id["firms_viirs"]["status"] == "ok"
    assert por_id["firms_viirs"]["records"] > 0


def test_seviri_is_disabled_not_broken(firms_simulado, salidas):
    """RF-P-02 no está implementado. Sin ingesta, la fuente no ha fallado."""
    _ejecutar(salidas)
    salud = json.loads(salidas.sources_json.read_text(encoding="utf-8"))

    seviri = next(s for s in salud["sources"] if s["id"] == "seviri")
    assert seviri["status"] == "disabled"


# --- guardas -----------------------------------------------------------------


def test_aborts_when_firms_returns_nothing(monkeypatch, salidas):
    """Sin datos de FIRMS no se publica: las salidas anteriores se conservan."""
    monkeypatch.setattr(
        pipeline.firms, "fetch_hotspots", lambda **_: pd.DataFrame()
    )

    with pytest.raises(SystemExit):
        _ejecutar(salidas)

    assert not salidas.manifest.exists()


def test_aborts_on_suspicious_emptiness_against_history(firms_simulado, salidas, monkeypatch):
    """Un desplome respecto a la mediana reciente aborta antes de publicar."""
    from incendios import publish

    publish.save_history(
        salidas.runs_json,
        [{"hotspots": 2000, "incidents": 40, "at": "2026-07-27T12:00:00Z"}] * 10,
    )

    with pytest.raises(publish.SuspiciousEmptiness):
        _ejecutar(salidas)

    assert not salidas.manifest.exists()


def test_manifest_is_written_last(firms_simulado, salidas, monkeypatch):
    """RF-P-11 · si sources.json falla, el manifiesto no llega a escribirse.

    Es lo único que hace atómica la publicación: mientras el manifiesto no
    cambie, el frontend da por buena la ejecución anterior.
    """
    from incendios import health

    def romper(self, path, now=None):
        raise OSError("disco lleno")

    monkeypatch.setattr(health.HealthReport, "write", romper)

    with pytest.raises(OSError):
        _ejecutar(salidas)

    assert salidas.incidents_geojson.exists()  # los datos sí se escribieron
    assert not salidas.manifest.exists()  # el manifiesto no


def test_run_history_is_recorded_after_success(firms_simulado, salidas):
    from incendios import publish

    _ejecutar(salidas)

    historial = publish.load_history(salidas.runs_json)
    assert len(historial) == 1
    assert historial[0]["hotspots"] > 0


def test_run_history_is_not_polluted_by_aborted_runs(salidas, monkeypatch):
    """Anotar una ejecución fallida bajaría la mediana y desactivaría la guarda
    justo cuando más falta hace."""
    from incendios import publish

    monkeypatch.setattr(pipeline.firms, "fetch_hotspots", lambda **_: pd.DataFrame())

    with pytest.raises(SystemExit):
        _ejecutar(salidas)

    assert publish.load_history(salidas.runs_json) == []


# --- repetibilidad -----------------------------------------------------------


def test_two_consecutive_runs_are_stable(firms_simulado, salidas):
    """Los `fire_id` no pueden cambiar entre ejecuciones: de eso depende el
    enlace permanente de RF-F-02."""
    primera = _ejecutar(salidas)
    ids_1 = _ids_publicados(salidas)

    segunda = _ejecutar(salidas)
    ids_2 = _ids_publicados(salidas)

    assert ids_1 == ids_2
    assert primera["counts"]["incidents_total"] == segunda["counts"]["incidents_total"]


def test_history_append_is_idempotent_across_runs(firms_simulado, salidas):
    _ejecutar(salidas)
    _ejecutar(salidas)

    total = sum(
        len(pd.read_parquet(p / "part.parquet"))
        for p in salidas.history_dir.iterdir()
        if p.is_dir()
    )
    manifest = json.loads(salidas.manifest.read_text(encoding="utf-8"))
    assert total == manifest["counts"]["hotspots_24h"]


def _ids_publicados(salidas) -> set[str]:
    datos = json.loads(salidas.incidents_geojson.read_text(encoding="utf-8"))
    return {f["properties"]["id"] for f in datos["features"]}


# --- contadores fieles -------------------------------------------------------


def test_each_filter_is_counted_separately(firms_simulado, salidas):
    """Los duplicados no pueden contarse como supresiones industriales.

    La primera ejecución real publicó "464 focos industriales suprimidos"
    cuando eran 464 duplicados entre pasadas de NOAA-20 y NOAA-21, y la
    máscara no había descartado ninguno. El riesgo 3 de la sección 11 exige
    que ese número sea cierto: si algún día la máscara oculta un incendio
    real, es la única pista de por dónde mirar.
    """
    manifest = _ejecutar(salidas)
    counts = manifest["counts"]

    # El fixture mete 8 hotspots de baja confianza y 5 sobre Puertollano.
    assert counts["hotspots_suppressed_lowconf"] == 8
    assert counts["hotspots_suppressed_industrial"] == 5
    # Y los duplicados van aparte, no sumados a ninguno de los dos.
    assert counts["hotspots_deduplicated"] >= 0


def test_published_hotspots_carry_the_instrument(firms_simulado, salidas):
    """Sin `instrument` el filtro de sensor de RF-F-09 falla en silencio.

    El frontend hace `coalesce(instrument, 'VIIRS')`, así que apagar MODIS no
    haría nada y apagar VIIRS lo ocultaría todo. Y el manifiesto no podría
    publicar la antigüedad por familia de sensor.
    """
    import json

    _ejecutar(salidas)
    datos = json.loads(salidas.hotspots_geojson.read_text(encoding="utf-8"))

    props = datos["features"][0]["properties"]
    assert "instrument" in props
    assert props["instrument"]


def test_verifier_agrees_with_the_published_manifest(firms_simulado, salidas, monkeypatch):
    """El verificador y el manifiesto tienen que calcular la misma antigüedad.

    Discrepaban porque hotspots.geojson no llevaba `instrument`: el verificador
    no podía separar por familia de sensor y comparaba contra el foco más
    reciente en vez de contra el más antiguo por familia.
    """
    from incendios.config import OUTPUTS as REALES

    _ejecutar(salidas)

    # `verificar_datos` lee de OUTPUTS; se le apuntan las salidas del test.
    monkeypatch.setattr("incendios.config.OUTPUTS", salidas, raising=False)
    import importlib
    import sys

    sys.path.insert(0, str(REALES.manifest.parent.parent.parent / "scripts"))
    verif = importlib.import_module("verificar_datos")
    monkeypatch.setattr(verif, "OUTPUTS", salidas)

    informe = verif.verificar()

    assert informe.fallos == 0, [t for m, t in informe.lineas if m == verif.FALLO]


def test_the_fixture_does_not_go_stale_with_the_calendar(firms_simulado, salidas):
    """Regresión: el fixture usaba marcas de tiempo fijas y el pipeline compara
    contra la hora real, así que al pasar de medianoche los datos superaban las
    24 h y la regla de "cluster satelital sin detecciones recientes no se
    publica" los filtraba todos. La prueba pasaba el día que se escribió y
    fallaba al siguiente, con 0 incidentes y sin explicar por qué.
    """
    manifest = _ejecutar(salidas)

    assert manifest["counts"]["incidents_total"] > 0
    # El dato más viejo del fixture son 6 h; con margen para la ejecución.
    assert manifest["worst_data_age_seconds"] < 8 * 3600
