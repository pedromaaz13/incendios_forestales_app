"""Materialización de salidas · RF-P-11, RF-P-14, tabla 8.1 (filas 17-18).

Todo lo que este módulo escribe lo lee un navegador sin intermediario, así que
el contrato de campos y el formato de fecha son API pública: cambiarlos rompe el
frontend en silencio.

Los tests escriben en `tmp_path` a través del fixture `tmp_outputs`. Ninguno
toca `data/` del repo.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest
from conftest import make_hotspots, to_gdf

from incendios import cluster as cluster_mod
from incendios import export as export_mod


@pytest.fixture
def pipeline_data(now):
    """Salida realista de las fases previas: hotspots etiquetados, fuegos, perímetros."""
    entrada = pd.concat(
        [
            make_hotspots(40.25, -6.60, n=12, spread_deg=0.005, frp=30.0, hours_ago=[2, 3]),
            make_hotspots(42.40, -7.85, n=3, spread_deg=0.002, frp=8.0, hours_ago=4),
        ],
        ignore_index=True,
    )
    hotspots = cluster_mod.assign_fire_ids(to_gdf(entrada))
    fires = cluster_mod.build_fires(hotspots, now=now)
    fires["municipio"] = "Municipio"
    fires["provincia"] = "Provincia"
    perimeters = cluster_mod.build_perimeters(hotspots)
    return hotspots, fires, perimeters


# --- contrato de campos del GeoJSON -----------------------------------------


def test_geojson_only_carries_the_web_contract(tmp_outputs, pipeline_data):
    """Cada propiedad extra se multiplica por el número de features.

    `brightness_k`, `scan`, `track` y compañía se quedan en el Parquet: el
    presupuesto de RNF-02 son 900 KB para toda la carga inicial.
    """
    hotspots, _, _ = pipeline_data

    export_mod._write_geojson(
        hotspots, tmp_outputs.hotspots_geojson, export_mod.HOTSPOT_WEB_FIELDS
    )

    data = json.loads(tmp_outputs.hotspots_geojson.read_text(encoding="utf-8"))
    props = set(data["features"][0]["properties"])
    assert props == set(export_mod.HOTSPOT_WEB_FIELDS)
    assert "brightness_k" not in props
    assert "scan" not in props


def test_fires_geojson_carries_the_fire_contract(tmp_outputs, pipeline_data):
    _, fires, _ = pipeline_data

    export_mod._write_geojson(fires, tmp_outputs.fires_geojson, export_mod.FIRE_WEB_FIELDS)

    data = json.loads(tmp_outputs.fires_geojson.read_text(encoding="utf-8"))
    props = set(data["features"][0]["properties"])
    assert {"fire_id", "status", "intensity", "n_hotspots", "area_est_ha"} <= props


def test_dates_are_serialised_as_utc_iso8601(tmp_outputs, pipeline_data):
    """El frontend hace `new Date(...)`. Sin la Z lo interpreta como hora local
    y la latencia publicada sale desplazada dos horas en España."""
    hotspots, _, _ = pipeline_data

    export_mod._write_geojson(
        hotspots, tmp_outputs.hotspots_geojson, export_mod.HOTSPOT_WEB_FIELDS
    )

    data = json.loads(tmp_outputs.hotspots_geojson.read_text(encoding="utf-8"))
    acq = data["features"][0]["properties"]["acq_dt"]
    assert acq.endswith("Z")
    assert pd.Timestamp(acq).tz is not None


def test_isoformat_leaves_non_datetime_columns_alone(pipeline_data):
    _, fires, _ = pipeline_data

    out = export_mod._isoformat(fires)

    assert out["fire_id"].tolist() == fires["fire_id"].tolist()
    assert out["first_detected"].str.endswith("Z").all()


# --- histórico ---------------------------------------------------------------


def test_history_is_partitioned_by_acquisition_date(tmp_outputs, pipeline_data):
    hotspots, _, _ = pipeline_data

    export_mod.write_history(hotspots)

    particiones = sorted(p.name for p in tmp_outputs.history_dir.iterdir())
    assert particiones
    assert all(p.startswith("acq_date=") for p in particiones)


def test_history_append_is_idempotent(tmp_outputs, pipeline_data):
    """El cron corre cada 10 min sobre una ventana de 3 días: el solape es la
    norma, no la excepción. Sin idempotencia el histórico se infla sin límite."""
    hotspots, _, _ = pipeline_data

    export_mod.write_history(hotspots)
    export_mod.write_history(hotspots)
    export_mod.write_history(hotspots)

    total = sum(
        len(pd.read_parquet(p / "part.parquet"))
        for p in tmp_outputs.history_dir.iterdir()
    )
    assert total == len(hotspots)


def test_history_accumulates_new_detections(tmp_outputs, pipeline_data, now):
    hotspots, _, _ = pipeline_data
    export_mod.write_history(hotspots)

    nuevos = cluster_mod.assign_fire_ids(
        to_gdf(make_hotspots(37.50, -3.20, n=2, spread_deg=0.002, hours_ago=1.0))
    )
    export_mod.write_history(nuevos)

    total = sum(
        len(pd.read_parquet(p / "part.parquet"))
        for p in tmp_outputs.history_dir.iterdir()
    )
    assert total == len(hotspots) + len(nuevos)


# --- PMTiles -----------------------------------------------------------------


def test_pmtiles_is_skipped_without_tippecanoe(tmp_outputs, monkeypatch, caplog):
    """tippecanoe es opcional: sin él se pierde la capa a escala, no el pipeline."""
    monkeypatch.setattr(export_mod.shutil, "which", lambda _: None)

    with caplog.at_level("WARNING"):
        ok = export_mod.write_pmtiles(
            tmp_outputs.hotspots_geojson, tmp_outputs.hotspots_pmtiles, layer="hotspots"
        )

    assert ok is False
    assert any("tippecanoe" in r.getMessage() for r in caplog.records)


# --- manifest ----------------------------------------------------------------


def test_manifest_publishes_data_age(tmp_outputs, pipeline_data):
    """RF-F-05 y el principio rector: la latencia se publica siempre."""
    hotspots, fires, _ = pipeline_data

    manifest = export_mod.write_manifest(hotspots, fires)

    assert manifest["data_age_minutes"] is not None
    assert manifest["data_age_minutes"] >= 0
    assert manifest["last_detection_at"].endswith("Z")
    assert manifest["generated_at"].endswith("Z")


def test_manifest_counts_match_the_inputs(tmp_outputs, pipeline_data):
    hotspots, fires, _ = pipeline_data

    manifest = export_mod.write_manifest(hotspots, fires)

    assert manifest["hotspots"] == len(hotspots)
    assert manifest["fires_total"] == len(fires)
    assert manifest["fires_active"] == int((fires["status"] == "activo").sum())
    assert manifest["frp_total_mw"] == pytest.approx(float(fires["frp_total_mw"].sum()))


def test_manifest_carries_the_112_disclaimer(tmp_outputs, pipeline_data):
    """RF-F-12: el aviso viaja con los datos, no solo en el HTML.

    Si alguien consume `manifest.json` directamente, el aviso va incluido.
    """
    hotspots, fires, _ = pipeline_data

    manifest = export_mod.write_manifest(hotspots, fires)

    assert "112" in manifest["disclaimer"]
    assert "no son información oficial" in manifest["disclaimer"].lower()


def test_manifest_with_no_hotspots_reports_null_age(tmp_outputs, pipeline_data):
    """Sin datos, la latencia es `null`, nunca 0.

    Un 0 se pinta verde en el panel de RF-F-05 y diría "datos de hace un
    momento" justo cuando no hay datos.
    """
    hotspots, fires, _ = pipeline_data
    vacios = hotspots.iloc[0:0]

    manifest = export_mod.write_manifest(vacios, fires.iloc[0:0])

    assert manifest["data_age_minutes"] is None
    assert manifest["last_detection_at"] is None
    assert manifest["hotspots"] == 0


def test_manifest_is_written_to_disk(tmp_outputs, pipeline_data):
    hotspots, fires, _ = pipeline_data

    manifest = export_mod.write_manifest(hotspots, fires)

    en_disco = json.loads(tmp_outputs.manifest.read_text(encoding="utf-8"))
    assert en_disco == manifest


# --- export_all --------------------------------------------------------------


def test_export_all_writes_every_artifact(tmp_outputs, pipeline_data, monkeypatch):
    monkeypatch.setattr(export_mod.shutil, "which", lambda _: None)
    hotspots, fires, perimeters = pipeline_data

    manifest = export_mod.export_all(hotspots, fires, perimeters)

    assert tmp_outputs.hotspots_geojson.exists()
    assert tmp_outputs.fires_geojson.exists()
    assert tmp_outputs.perimeters_geojson.exists()
    assert tmp_outputs.manifest.exists()
    assert manifest["fires_total"] == len(fires)


def test_export_all_geojson_is_valid_featurecollection(tmp_outputs, pipeline_data, monkeypatch):
    monkeypatch.setattr(export_mod.shutil, "which", lambda _: None)
    hotspots, fires, perimeters = pipeline_data

    export_mod.export_all(hotspots, fires, perimeters)

    for path in (
        tmp_outputs.hotspots_geojson,
        tmp_outputs.fires_geojson,
        tmp_outputs.perimeters_geojson,
    ):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["type"] == "FeatureCollection"
        assert len(data["features"]) > 0


# --- pendiente: RF-P-11 aborto por vaciado sospechoso ------------------------


@pytest.mark.skip(
    reason=(
        "RF-P-11 sin implementar (sesión 3 de docs/PROMPTS.md). Hoy export_all() "
        "escribe lo que le den, incluido un DataFrame vacío, y eso publica "
        "'no hay incendios' cuando lo que hay es una fuente caída. Falta la "
        "comparación contra la mediana de las últimas ejecuciones."
    )
)
def test_aborts_on_suspicious_emptiness(tmp_outputs, pipeline_data):
    """0 hotspots tras un histórico de cientos es un fallo de fuente, no calma."""
    hotspots, fires, perimeters = pipeline_data
    export_mod.export_all(hotspots, fires, perimeters)

    with pytest.raises(SystemExit):
        export_mod.export_all(hotspots.iloc[0:0], fires.iloc[0:0], perimeters.iloc[0:0])


# Los ocho invariantes de la sección 4.4 viven en tests/test_invariants.py.
