"""Ingesta NASA FIRMS · RF-P-01, tabla 8.1 (filas 1-3).

El fallo peligroso de este módulo no es la excepción: es el silencio. FIRMS
responde HTTP 200 con un cuerpo de texto plano cuando la clave se agota, y ese
cuerpo parseado como CSV produce cero filas, que aguas abajo se lee como "hoy no
hay incendios en España". Los tests de este fichero cubren sobre todo eso.
"""

from __future__ import annotations

import httpx
import pandas as pd
import pytest
from conftest import read_fixture

from incendios import firms


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _csv_response(body: str):
    return lambda request: httpx.Response(200, text=body)


# --- tabla 8.1: FIRMS devuelve HTML de error con HTTP 200 -------------------


def test_detects_non_csv_response(caplog):
    """RF-P-01: cuota agotada llega como 200 + texto plano. Vacío, sin lanzar."""
    body = read_fixture("firms_quota_exhausted.txt")

    with caplog.at_level("WARNING"), _client(_csv_response(body)) as client:
        df = firms._fetch_one(client, "VIIRS_NOAA20_NRT", "peninsula", "-9,35,4,44")

    assert df.empty
    assert any("no-CSV" in r.getMessage() for r in caplog.records)


def test_detects_html_error_page():
    """Misma clase de fallo servida como HTML en lugar de texto plano."""
    body = "<html><body><h1>Service Unavailable</h1></body></html>"

    with _client(_csv_response(body)) as client:
        df = firms._fetch_one(client, "MODIS_NRT", "canarias", "-18,27,-13,29")

    assert df.empty


def test_empty_body_is_not_confused_with_zero_fires():
    with _client(_csv_response("")) as client:
        assert firms._fetch_one(client, "VIIRS_SNPP_NRT", "baleares", "1,38,4,40").empty


def test_http_error_returns_empty_without_raising(caplog):
    """Un 500 de FIRMS no puede tumbar la ingesta de los otros tres sensores."""
    handler = lambda request: httpx.Response(500, text="boom")

    with caplog.at_level("WARNING"), _client(handler) as client:
        df = firms._fetch_one(client, "VIIRS_NOAA21_NRT", "peninsula", "-9,35,4,44")

    assert df.empty
    assert any("falló" in r.getMessage() for r in caplog.records)


def test_fetch_hotspots_returns_typed_empty_frame_when_all_sources_fail(monkeypatch):
    """Si ninguna petición trae datos, el DataFrame vacío conserva el esquema.

    Aguas abajo `clean()` indexa columnas por nombre: un DataFrame vacío sin
    columnas convertiría un fallo de red en un KeyError a 200 líneas de
    distancia del origen.
    """
    monkeypatch.setattr(firms, "FIRMS_MAP_KEY", "clave-de-prueba")
    monkeypatch.setattr(
        firms, "_fetch_one", lambda *a, **k: pd.DataFrame()
    )

    df = firms.fetch_hotspots(persist_raw=False)

    assert df.empty
    assert list(df.columns) == firms.SCHEMA


def test_fetch_hotspots_requires_map_key(monkeypatch):
    monkeypatch.setattr(firms, "FIRMS_MAP_KEY", "")
    with pytest.raises(RuntimeError, match="FIRMS_MAP_KEY"):
        firms.fetch_hotspots(persist_raw=False)


# --- tabla 8.1: acq_time = 45 sin ceros a la izquierda ----------------------


def test_acq_time_without_leading_zeros():
    """`45` es 00:45, no las 45:00 ni las 04:50."""
    raw = _read_csv_fixture("firms_viirs_snpp.csv")
    out = firms._normalize(raw)

    primera = out.sort_values("acq_dt")["acq_dt"].iloc[0]
    assert primera == pd.Timestamp("2026-07-27T00:45:00Z")


def test_acq_time_midnight_and_noon():
    raw = _synthetic_csv(acq_time=[0, 5, 45, 1312, 2359])
    out = firms._normalize(raw)

    assert out["acq_dt"].dt.strftime("%H:%M").tolist() == [
        "00:00",
        "00:05",
        "00:45",
        "13:12",
        "23:59",
    ]


# --- tabla 8.1: CSV con columna faltante ------------------------------------


def test_missing_column_raises_clear_error():
    """Tabla 8.1: excepción clara, no `KeyError` opaco.

    Una fila con `latitude` a nulo sería peor que la excepción: se propagaría
    hasta el GeoJSON como un incendio en el golfo de Guinea. Pero el mensaje
    tiene que decir qué sensor, qué área y qué columnas llegaron, o depurarlo en
    plena temporada cuesta una tarde.
    """
    raw = _synthetic_csv().drop(columns=["latitude"])

    with pytest.raises(ValueError, match="latitude") as exc:
        firms._normalize(raw)

    mensaje = str(exc.value)
    assert "VIIRS_NOAA20_NRT" in mensaje
    assert "peninsula" in mensaje
    assert "longitude" in mensaje  # lista de columnas recibidas


def test_missing_brightness_column_is_reported():
    """VIIRS trae `bright_ti4` y MODIS `brightness`. Sin ninguno, no hay hotspot."""
    raw = _synthetic_csv().drop(columns=["bright_ti4"])

    with pytest.raises(ValueError, match="bright_ti4"):
        firms._normalize(raw)


def test_optional_column_absent_does_not_crash():
    """`daynight` es opcional: su ausencia deja la columna vacía, no rompe."""
    raw = _synthetic_csv().drop(columns=["daynight"])

    out = firms._normalize(raw)

    assert "daynight" in out.columns


# --- normalización de esquema VIIRS / MODIS ---------------------------------


def test_normalizes_viirs_schema():
    out = firms._normalize(_read_csv_fixture("firms_viirs_snpp.csv"))

    assert list(out.columns) == firms.SCHEMA
    assert out["brightness_k"].notna().all()
    assert str(out["acq_dt"].dt.tz) == "UTC"


def test_normalizes_modis_schema():
    """MODIS trae `brightness` y `confidence` numérico; VIIRS `bright_ti4` y letra."""
    out = firms._normalize(_read_csv_fixture("firms_modis.csv"))

    assert list(out.columns) == firms.SCHEMA
    assert out["confidence_pct"].tolist() == [78.0, 12.0]
    assert out["brightness_k"].iloc[0] == pytest.approx(321.4)


def test_categorical_confidence_maps_to_percentage():
    out = firms._normalize(_read_csv_fixture("firms_viirs_snpp.csv"))

    conf = dict(zip(out["confidence_raw"], out["confidence_pct"], strict=True))
    assert conf["l"] == 20.0
    assert conf["n"] == 60.0
    assert conf["h"] == 90.0


def test_missing_frp_becomes_zero_not_nan():
    """Un FRP nulo suma 0 al agregado; un NaN lo envenena entero."""
    raw = _synthetic_csv()
    raw["frp"] = [None] * len(raw)

    out = firms._normalize(raw)

    assert (out["frp_mw"] == 0.0).all()


def test_normalize_empty_frame_keeps_schema():
    out = firms._normalize(pd.DataFrame())

    assert out.empty
    assert list(out.columns) == firms.SCHEMA


# --- deduplicación entre bboxes solapados -----------------------------------


def test_fetch_hotspots_deduplicates_overlapping_areas(monkeypatch):
    """Península y Baleares se solapan en el Mediterráneo: el mismo píxel llega
    dos veces y no puede contarse como dos detecciones."""
    monkeypatch.setattr(firms, "FIRMS_MAP_KEY", "clave-de-prueba")

    raw = _read_csv_fixture("firms_viirs_snpp.csv")

    def fake_fetch(client, source, area_key, area):
        block = raw.copy()
        block["source"] = source
        block["area_key"] = area_key
        return block

    monkeypatch.setattr(firms, "_fetch_one", fake_fetch)
    monkeypatch.setattr(firms, "AREAS", {"peninsula": "a", "baleares": "b"})
    monkeypatch.setattr(firms, "FIRMS_SOURCES", ("VIIRS_NOAA20_NRT",))

    df = firms.fetch_hotspots(persist_raw=False)

    # Mismo sensor y mismo píxel en dos áreas -> una sola fila por detección.
    assert len(df) == len(raw)
    assert df["acq_dt"].is_monotonic_increasing


# --- utilidades del propio fichero ------------------------------------------


def _read_csv_fixture(name: str) -> pd.DataFrame:
    import io

    df = pd.read_csv(io.StringIO(read_fixture(name)))
    df["source"] = "VIIRS_NOAA20_NRT"
    df["area_key"] = "peninsula"
    return df


def _synthetic_csv(acq_time: list[int] | None = None) -> pd.DataFrame:
    acq_time = acq_time or [1200, 1312]
    n = len(acq_time)
    return pd.DataFrame(
        {
            "latitude": [40.0 + i * 0.01 for i in range(n)],
            "longitude": [-6.0] * n,
            "acq_date": ["2026-07-27"] * n,
            "acq_time": acq_time,
            "satellite": ["N"] * n,
            "instrument": ["VIIRS"] * n,
            "confidence": ["n"] * n,
            "bright_ti4": [335.0] * n,
            "frp": [10.0] * n,
            "daynight": ["D"] * n,
            "scan": [0.4] * n,
            "track": [0.4] * n,
            "source": ["VIIRS_NOAA20_NRT"] * n,
            "area_key": ["peninsula"] * n,
        }
    )


# --- Reintentos ante fallo de transporte ------------------------------------
#
# Medido en producción: 2 de 12 ejecuciones murieron con `Network is unreachable`
# en las 12 peticiones a la vez, mientras la anterior y la siguiente funcionaban.
# Era la red del runner, no FIRMS. Sin reintento ese segundo cuesta media hora de
# datos, porque el cron no vuelve hasta la siguiente marca.


def test_un_fallo_de_red_transitorio_se_reintenta(monkeypatch):
    monkeypatch.setattr(firms, "ESPERA_BASE_S", 0.0)  # sin dormir en la prueba
    intentos = {"n": 0}

    def handler(request):
        intentos["n"] += 1
        if intentos["n"] < 3:
            raise httpx.ConnectError("Network is unreachable")
        return httpx.Response(200, text=read_fixture("firms_viirs_snpp.csv"))

    df = firms._fetch_one(_client(handler), "VIIRS_SNPP_NRT", "peninsula", "1,2,3,4")

    assert intentos["n"] == 3, "debería haber reintentado dos veces"
    assert not df.empty, "el tercer intento trajo datos y deben conservarse"


def test_se_rinde_tras_agotar_los_intentos(monkeypatch):
    monkeypatch.setattr(firms, "ESPERA_BASE_S", 0.0)
    intentos = {"n": 0}

    def handler(request):
        intentos["n"] += 1
        raise httpx.ConnectError("Network is unreachable")

    df = firms._fetch_one(_client(handler), "MODIS_NRT", "canarias", "1,2,3,4")

    assert intentos["n"] == firms.INTENTOS
    assert df.empty, "sin datos se devuelve vacío, no se propaga la excepción"


def test_una_clave_agotada_no_se_reintenta(monkeypatch):
    """FIRMS responde 200 con texto plano cuando la clave está agotada.

    Repetir eso no la arregla: solo gastaría más cuota y retrasaría el aborto.
    Solo se reintentan los fallos de transporte.
    """
    monkeypatch.setattr(firms, "ESPERA_BASE_S", 0.0)
    intentos = {"n": 0}

    def handler(request):
        intentos["n"] += 1
        return httpx.Response(200, text="Invalid MAP_KEY.")

    df = firms._fetch_one(_client(handler), "VIIRS_SNPP_NRT", "peninsula", "1,2,3,4")

    assert intentos["n"] == 1, "una respuesta no-CSV no debe reintentarse"
    assert df.empty


# --- Cuota de FIRMS ---------------------------------------------------------
#
# FIRMS **no** manda la cuota en ninguna cabecera: se comprobó el 30-07-2026 y
# las respuestas de la API de área solo traen `x-frame-options` y
# `x-content-type-options`. La primera versión de esto leía una cabecera copiada
# de AEMET y publicaba un campo que salía siempre nulo. El dato real está en un
# endpoint aparte, y estas pruebas fijan su esquema.


def _cuota(payload: dict | str, status: int = 200):
    def handler(request):
        assert "MAP_KEY" in str(request.url), "la clave debe ir como parámetro"
        if isinstance(payload, str):
            return httpx.Response(status, text=payload)
        return httpx.Response(status, json=payload)

    return handler


def test_la_cuota_se_calcula_como_limite_menos_usadas(monkeypatch):
    monkeypatch.setattr(firms, "FIRMS_MAP_KEY", "clave-de-prueba")
    monkeypatch.setattr(firms, "cuota_restante", None)
    handler = _cuota({
        "transaction_limit": 5000,
        "current_transactions": 54,
        "transaction_interval": "10 minutes",
    })

    assert firms.consultar_cuota(_client(handler)) == 4946
    assert firms.cuota_restante == 4946
    assert firms.cuota_limite == 5000


def test_una_cuota_agotada_no_da_negativo(monkeypatch):
    """Cero es el suelo: un negativo se leería como un valor imposible y podría
    colarse en una comparación al revés."""
    monkeypatch.setattr(firms, "FIRMS_MAP_KEY", "clave-de-prueba")
    monkeypatch.setattr(firms, "cuota_restante", None)
    handler = _cuota({"transaction_limit": 5000, "current_transactions": 6000})

    assert firms.consultar_cuota(_client(handler)) == 0


def test_sin_clave_no_se_consulta_la_cuota(monkeypatch):
    monkeypatch.setattr(firms, "FIRMS_MAP_KEY", "")
    monkeypatch.setattr(firms, "cuota_restante", None)

    assert firms.consultar_cuota() is None


def test_un_esquema_distinto_no_tumba_la_ingesta(monkeypatch):
    """La cuota es telemetría. Si FIRMS cambia el formato se avisa y se sigue:
    quedarse sin mapa por no poder leer un contador sería desproporcionado."""
    monkeypatch.setattr(firms, "FIRMS_MAP_KEY", "clave-de-prueba")
    monkeypatch.setattr(firms, "cuota_restante", None)
    handler = _cuota({"limite": 5000})

    assert firms.consultar_cuota(_client(handler)) is None
    assert firms.cuota_restante is None


def test_una_respuesta_no_json_no_tumba_la_ingesta(monkeypatch):
    monkeypatch.setattr(firms, "FIRMS_MAP_KEY", "clave-de-prueba")
    monkeypatch.setattr(firms, "cuota_restante", None)
    handler = _cuota("MAP_KEY is invalid or your have exceeded your transaction limit.")

    assert firms.consultar_cuota(_client(handler)) is None
