"""Estado de fuentes · RF-P-10.

Lo que se prueba aquí es una frase: la diferencia entre "Castilla-La Mancha: 0
incendios" y "INFOCAM lleva 40 minutos sin responder". La primera es
desinformación; la segunda es lo que este fichero garantiza que se publique.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pandas as pd

from incendios import health
from incendios.health import (
    MAX_DATA_AGE_SECONDS,
    STATUS_DISABLED,
    STATUS_ERROR,
    STATUS_OK,
    STATUS_STALE,
    HealthReport,
    SourceHealth,
)

NOW = datetime(2026, 7, 27, 18, 0, 0, tzinfo=UTC)


def _fuente(**kwargs) -> SourceHealth:
    base = {
        "id": "jcyl",
        "name": "Junta de Castilla y León",
        "region": "Castilla y León",
        "kind": "oficial",
        "ttl_seconds": 300,
        "last_success_at": NOW - timedelta(seconds=120),
        "records": 12,
    }
    return SourceHealth(**{**base, **kwargs})


# --- los cuatro estados ------------------------------------------------------


def test_status_ok_within_ttl():
    assert _fuente(last_success_at=NOW - timedelta(seconds=200)).status(NOW) == STATUS_OK


def test_status_stale_beyond_three_ttls():
    """x3 el TTL da margen a dos fallos transitorios antes de alarmar."""
    vieja = _fuente(last_success_at=NOW - timedelta(seconds=301 * 3))

    assert vieja.status(NOW) == STATUS_STALE


def test_status_ok_just_under_the_stale_threshold():
    justa = _fuente(last_success_at=NOW - timedelta(seconds=300 * 3))

    assert justa.status(NOW) == STATUS_OK


def test_status_error_when_last_attempt_failed():
    assert _fuente(error="HTTP 500").status(NOW) == STATUS_ERROR


def test_status_error_when_never_succeeded():
    """Sin ningún éxito registrado no se puede afirmar que la fuente esté bien."""
    assert _fuente(last_success_at=None).status(NOW) == STATUS_ERROR


def test_status_disabled_when_not_configured():
    """Una fuente sin endpoint no ha fallado: es que todavía no existe.

    Mezclarla con las caídas haría que el panel pareciese roto cuando solo está
    incompleto, y eso desgasta la confianza en el indicador.
    """
    sin_url = _fuente(configured=False, last_success_at=None, error="endpoint sin descubrir")

    assert sin_url.status(NOW) == STATUS_DISABLED


def test_age_seconds_is_none_without_success():
    assert _fuente(last_success_at=None).age_seconds(NOW) is None


def test_age_seconds_never_negative():
    """Un reloj adelantado en la fuente no debe publicar una edad negativa."""
    futura = _fuente(last_success_at=NOW + timedelta(seconds=60))

    assert futura.age_seconds(NOW) == 0


# --- degradación global ------------------------------------------------------


def test_not_degraded_when_everything_is_ok():
    informe = HealthReport([_fuente(), _fuente(id="112cv", name="112 CV")])

    degradado, motivo = informe.degraded(NOW)

    assert degradado is False
    assert motivo is None


def test_degraded_when_a_critical_source_fails():
    informe = HealthReport([
        _fuente(id="jcyl", critical=True, error="HTTP 500"),
        _fuente(id="112cv", name="112 CV"),
    ])

    degradado, motivo = informe.degraded(NOW)

    assert degradado is True
    assert "Castilla y León" in motivo


def test_degraded_when_a_critical_source_is_stale():
    informe = HealthReport([
        _fuente(critical=True, last_success_at=NOW - timedelta(hours=3)),
    ])

    degradado, motivo = informe.degraded(NOW)

    assert degradado is True
    assert "stale" in motivo


def test_non_critical_failure_does_not_degrade():
    """Una comunidad caída no puede pintar de ámbar el mapa entero."""
    informe = HealthReport([
        _fuente(id="infocam", name="INFOCAM", critical=False, error="timeout"),
    ])

    assert informe.degraded(NOW)[0] is False


def test_degraded_when_data_is_older_than_four_hours():
    """RF-P-10: worst_data_age_seconds > 14400 degrada aunque todo responda."""
    informe = HealthReport([_fuente()])

    degradado, motivo = informe.degraded(NOW, worst_data_age_seconds=MAX_DATA_AGE_SECONDS + 60)

    assert degradado is True
    assert "4 h" in motivo


def test_not_degraded_just_under_the_age_threshold():
    informe = HealthReport([_fuente()])

    assert informe.degraded(NOW, worst_data_age_seconds=MAX_DATA_AGE_SECONDS)[0] is False


def test_degraded_reason_lists_every_cause():
    """Una banda ámbar que no dice qué pasa obliga al usuario a adivinar."""
    informe = HealthReport([_fuente(critical=True, error="HTTP 500")])

    _, motivo = informe.degraded(NOW, worst_data_age_seconds=MAX_DATA_AGE_SECONDS + 1)

    assert "críticas" in motivo
    assert "4 h" in motivo


# --- serialización -----------------------------------------------------------


def test_block_matches_the_4_2_contract():
    bloque = _fuente(precision_m=500.0).to_dict(NOW)

    assert set(bloque) == {
        "id", "name", "region", "kind", "critical", "status", "last_success_at",
        "age_seconds", "ttl_seconds", "records", "precision_m", "error",
        "consecutive_failures", "attribution",
        # Solo FIRMS la declara; el resto publica null, que el frontend
        # distingue de "cero peticiones restantes".
        "quota_remaining",
        "quota_limit",
    }
    assert bloque["last_success_at"].endswith("Z")
    assert bloque["age_seconds"] == 120


def test_failing_sources_are_listed_first():
    """RF-F-06: las fuentes en error van arriba, sin tener que desplazar."""
    informe = HealthReport([
        _fuente(id="ok1", name="Aaa correcta"),
        _fuente(id="err", name="Zzz caída", error="HTTP 500"),
        _fuente(id="stale", name="Mmm rancia", last_success_at=NOW - timedelta(hours=5)),
        _fuente(id="off", name="Bbb sin configurar", configured=False),
    ])

    orden = [s["id"] for s in informe.to_dict(NOW)["sources"]]

    assert orden[0] == "err"
    assert orden[1] == "stale"
    assert orden.index("off") < orden.index("ok1")


def test_write_produces_valid_json(tmp_path):
    informe = HealthReport([_fuente(), _fuente(id="112cv", name="112 CV", error="timeout")])
    destino = tmp_path / "live" / "sources.json"

    payload = informe.write(destino, now=NOW)

    en_disco = json.loads(destino.read_text(encoding="utf-8"))
    assert en_disco == payload
    assert en_disco["generated_at"] == "2026-07-27T18:00:00Z"
    assert len(en_disco["sources"]) == 2


def test_write_is_under_the_size_budget(tmp_path):
    """El contrato 3.1 da 8 KB a sources.json."""
    informe = HealthReport([_fuente(id=f"s{i}", name=f"Fuente {i}") for i in range(12)])
    destino = tmp_path / "sources.json"

    informe.write(destino, now=NOW)

    assert destino.stat().st_size < 8 * 1024


# --- construcción desde el registro de adaptadores ---------------------------


def test_unconfigured_registry_sources_are_disabled_not_broken():
    """Las cinco autonómicas siguen sin endpoint: `disabled`, no `error`."""
    from incendios.sources.adapters import REGISTRY

    estados = health.from_official_sources(REGISTRY, results={}, now=NOW)

    assert {s.id for s in estados} == {"jcyl", "infocam", "112cv", "bombers", "infoca"}
    assert all(s.status(NOW) == STATUS_DISABLED for s in estados)
    assert all(s.records == 0 for s in estados)


def test_registry_source_with_records_is_ok():
    from incendios.sources.base import SourceMeta

    class _Falsa:
        meta = SourceMeta(
            source_id="demo",
            name="Demo",
            region="Demo",
            url="https://demo.invalid/query",
            precision_m=500.0,
        )

    resultados = {"demo": pd.DataFrame({"external_id": ["a", "b"]})}

    estados = health.from_official_sources([_Falsa()], resultados, now=NOW)

    assert estados[0].status(NOW) == STATUS_OK
    assert estados[0].records == 2


def test_registry_source_configured_but_empty_is_error():
    """Configurada y sin filas: o no hay incendios, o el parseo se rompió.

    Se marca `error` a propósito. Un endpoint que devuelve vacío es
    indistinguible de uno que dejó de funcionar, y en este dominio la
    interpretación segura es la pesimista.
    """
    from incendios.sources.base import SourceMeta

    class _Falsa:
        meta = SourceMeta(
            source_id="demo", name="Demo", region="Demo",
            url="https://demo.invalid/query", precision_m=500.0,
        )

    estados = health.from_official_sources([_Falsa()], {"demo": pd.DataFrame()}, now=NOW)

    assert estados[0].status(NOW) == STATUS_ERROR


# --- cuota de la fuente ------------------------------------------------------


def test_la_cuota_se_publica_cuando_la_fuente_la_declara():
    """Agotar la cuota de FIRMS se manifiesta como «cero incendios».

    Publicar las peticiones restantes es lo que permite avisar antes de que
    pase, en vez de descubrirlo cuando el mapa ya está vacío.
    """
    bloque = _fuente(quota_remaining=37).to_dict(NOW)

    assert bloque["quota_remaining"] == 37


def test_sin_cuota_declarada_es_nulo_y_no_cero():
    """Cero significaría «no quedan peticiones». Nulo, «esta fuente no informa».

    Confundirlos pintaría de rojo Open-Meteo y la DGT, que no tienen cuota.
    """
    assert _fuente().to_dict(NOW)["quota_remaining"] is None
