"""Artefactos de `live/` · secciones 4.1 y 4.3.

El requisito con más peso de todo el proyecto vive aquí: **las dos latencias**.
`pipeline_age_seconds` dice cuándo corrió el pipeline; `data_age_seconds` dice
cuándo se tomó el dato. Pueden diferir en horas, y presentar el segundo con la
cara del primero es exactamente la desinformación que este visor existe para no
producir.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

from incendios import build

NOW = datetime(2026, 7, 27, 18, 0, 0, tzinfo=timezone.utc)
TS = pd.Timestamp(NOW)


def _hotspots(**kwargs) -> gpd.GeoDataFrame:
    base = {
        "acq_dt": [TS - timedelta(hours=2, minutes=19)],
        "instrument": ["VIIRS"],
        "frp_mw": [45.0],
    }
    base.update(kwargs)
    df = pd.DataFrame(base)
    return gpd.GeoDataFrame(df, geometry=[Point(-4.78, 40.41)] * len(df), crs=4326)


def _incidents(*filas: dict) -> gpd.GeoDataFrame:
    base = {
        "id": "abc123",
        "origin": "satelite",
        "satellite_confirmed": True,
        "official_confirmed": False,
        "confirmed_by": "",
        "status": "activo",
        "municipio": "Burgohondo",
        "provincia": "Ávila",
        "n_hotspots": 12,
        "frp_total_mw": 340.0,
        "intensity": "alta",
        "area_est_ha": 168.0,
        "position_precision_m": 375.0,
        "first_detected": TS - timedelta(hours=6),
        "last_detected": TS,
        "started_at": None,
    }
    datos = [{**base, **f} for f in (filas or [{}])]
    df = pd.DataFrame(datos)
    return gpd.GeoDataFrame(df, geometry=[Point(-4.78, 40.41)] * len(df), crs=4326)


# --- las dos latencias -------------------------------------------------------


def test_publishes_two_distinct_latencies():
    """RF-F-05: el dato es de hace 2 h 19 min; la ejecución, de hace 4 min."""
    manifest = build.build_manifest(
        _hotspots(),
        _incidents(),
        pipeline_started_at=NOW - timedelta(minutes=4),
        now=NOW,
    )

    assert manifest["pipeline_age_seconds"] == 240
    assert manifest["data_age_seconds"]["firms_viirs"] == 8340
    assert manifest["pipeline_age_seconds"] != manifest["worst_data_age_seconds"]


def test_data_age_is_broken_down_by_sensor_family():
    """Promediar VIIRS (horas) con SEVIRI (minutos) da un número que no
    describe a ninguno de los dos."""
    hotspots = _hotspots(
        acq_dt=[TS - timedelta(hours=3), TS - timedelta(minutes=15), TS - timedelta(hours=1)],
        instrument=["VIIRS", "SEVIRI", "MODIS"],
        frp_mw=[10.0, 20.0, 30.0],
    )

    edades = build.build_manifest(hotspots, _incidents(), now=NOW)["data_age_seconds"]

    assert edades["firms_viirs"] == 10800
    assert edades["seviri"] == 900
    assert edades["firms_modis"] == 3600


def test_worst_data_age_is_the_maximum():
    hotspots = _hotspots(
        acq_dt=[TS - timedelta(hours=3), TS - timedelta(minutes=15)],
        instrument=["VIIRS", "SEVIRI"],
        frp_mw=[10.0, 20.0],
    )

    manifest = build.build_manifest(hotspots, _incidents(), now=NOW)

    assert manifest["worst_data_age_seconds"] == 10800


def test_official_age_comes_from_reported_at():
    official = pd.DataFrame({"reported_at": [TS - timedelta(minutes=5)]})

    edades = build.build_manifest(
        _hotspots(), _incidents(), official=official, now=NOW
    )["data_age_seconds"]

    assert edades["official"] == 300


def test_no_hotspots_means_no_satellite_age():
    """Sin datos, la edad no existe. Publicar 0 diría 'recién actualizado'."""
    manifest = build.build_manifest(_hotspots().iloc[0:0], _incidents(), now=NOW)

    assert manifest["data_age_seconds"] == {}
    assert manifest["worst_data_age_seconds"] is None


def test_ages_are_never_negative():
    """Un reloj de la fuente adelantado no puede publicar una edad negativa."""
    futuro = _hotspots(acq_dt=[TS + timedelta(minutes=30)])

    edades = build.build_manifest(futuro, _incidents(), now=NOW)["data_age_seconds"]

    assert edades["firms_viirs"] == 0


# --- degradación -------------------------------------------------------------


def test_old_data_degrades_even_when_sources_respond():
    """Una fuente que contesta rápido con datos de hace 5 h sigue dando un mapa
    con el que no se puede decidir nada."""
    viejo = _hotspots(acq_dt=[TS - timedelta(hours=5)])

    manifest = build.build_manifest(viejo, _incidents(), now=NOW)

    assert manifest["degraded"] is True
    assert "4 h" in manifest["degraded_reason"]


def test_fresh_data_is_not_degraded():
    manifest = build.build_manifest(_hotspots(), _incidents(), now=NOW)

    assert manifest["degraded"] is False
    assert manifest["degraded_reason"] is None


def test_explicit_degradation_is_preserved():
    manifest = build.build_manifest(
        _hotspots(), _incidents(), degraded=True, degraded_reason="INFOCAM caída", now=NOW
    )

    assert manifest["degraded"] is True
    assert manifest["degraded_reason"] == "INFOCAM caída"


# --- contadores --------------------------------------------------------------


def test_counts_separate_satellite_from_official_only():
    incidentes = _incidents(
        {"id": "a", "satellite_confirmed": True, "official_confirmed": False, "origin": "satelite"},
        {"id": "b", "satellite_confirmed": True, "official_confirmed": True, "origin": "ambos"},
        {"id": "c", "satellite_confirmed": False, "official_confirmed": True, "origin": "oficial"},
    )

    counts = build.build_manifest(_hotspots(), incidentes, now=NOW)["counts"]

    assert counts["incidents_total"] == 3
    assert counts["incidents_satellite_confirmed"] == 2
    assert counts["incidents_official_only"] == 1


def test_suppressed_counts_are_published():
    """El riesgo 3 de la sección 11 exige registrar siempre lo suprimido: si la
    máscara oculta un incendio real, el número es la única pista."""
    counts = build.build_manifest(
        _hotspots(), _incidents(), suppressed_industrial=38, suppressed_lowconf=412, now=NOW
    )["counts"]

    assert counts["hotspots_suppressed_industrial"] == 38
    assert counts["hotspots_suppressed_lowconf"] == 412


def test_manifest_carries_the_112_disclaimer():
    manifest = build.build_manifest(_hotspots(), _incidents(), now=NOW)

    assert "112" in manifest["disclaimer"]
    assert "No es información oficial" in manifest["disclaimer"]


def test_manifest_timestamps_are_utc_iso8601():
    manifest = build.build_manifest(_hotspots(), _incidents(), now=NOW)

    assert manifest["generated_at"] == "2026-07-27T18:00:00Z"


def test_manifest_is_under_the_size_budget(tmp_path):
    """El contrato 3.1 da 4 KB a manifest.json."""
    destino = tmp_path / "manifest.json"

    build.write_manifest(build.build_manifest(_hotspots(), _incidents(), now=NOW), destino)

    assert destino.stat().st_size < 4 * 1024
    assert json.loads(destino.read_text(encoding="utf-8"))["schema_version"] == 1


# --- contrato 4.3 ------------------------------------------------------------


def test_web_output_matches_the_contract():
    web = build.incidents_for_web(_incidents())

    assert list(web.columns) == [*build.INCIDENT_WEB_FIELDS, "geometry"]


def test_web_output_drops_internal_columns():
    """`fire_id`, `area_key` y compañía se quedan en el Parquet."""
    incidentes = _incidents()
    incidentes["fire_id"] = "interno"
    incidentes["area_key"] = "peninsula"

    web = build.incidents_for_web(incidentes)

    assert "fire_id" not in web.columns
    assert "area_key" not in web.columns


def test_web_dates_are_iso_with_z():
    web = build.incidents_for_web(_incidents())

    assert web["last_detected"].iloc[0].endswith("Z")
    assert web["started_at"].iloc[0] is None


def test_web_output_survives_missing_optional_columns():
    """Sin fuentes oficiales configuradas no hay igr_level ni medios.

    El frontend lee esos campos igualmente: tienen que existir como nulos, no
    faltar, o el visor rompe al acceder a ellos.
    """
    minimo = _incidents().drop(columns=["igr_level"], errors="ignore")

    web = build.incidents_for_web(minimo)

    assert "igr_level" in web.columns
    assert web["igr_level"].isna().all()


@pytest.mark.parametrize("campo", ["igr_level", "resources_air", "resources_ground"])
def test_numeric_fields_are_coerced(campo):
    """Un '4 aéreos' en texto llegaría al navegador como string y rompería la
    ficha de RF-F-10 al formatearlo."""
    incidentes = _incidents({campo: "no es un número"})

    web = build.incidents_for_web(incidentes)

    assert pd.isna(web[campo].iloc[0])
