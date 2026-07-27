"""Publicación atómica y vaciado sospechoso · RF-P-11.

La regla que define este módulo: **es preferible no publicar que publicar una
mentira tranquilizadora**. Un "0 incendios activos" con marca de tiempo de hace
un minuto es el peor resultado posible de este sistema, peor que una caída, y las
dos guardas de aquí existen para eso.
"""

from __future__ import annotations

import json

import pytest

from incendios import publish
from incendios.publish import (
    EMPTINESS_DROP_RATIO,
    MIN_MEDIAN_FOR_CHECK,
    RunStats,
    SuspiciousEmptiness,
)


def _historial(*valores: int) -> list[dict]:
    return [{"hotspots": v, "incidents": 0, "at": "2026-07-27T12:00:00Z"} for v in valores]


# --- vaciado sospechoso ------------------------------------------------------


def test_aborts_on_suspicious_emptiness():
    """0 hotspots tras un histórico de cientos es un fallo de fuente."""
    with pytest.raises(SuspiciousEmptiness, match="Vaciado sospechoso"):
        publish.check_not_suspiciously_empty(0, _historial(*[2000] * 10))


def test_abort_message_explains_it_is_not_absence_of_fires():
    """Quien lea el log de Actions a las 4 de la mañana necesita el porqué."""
    with pytest.raises(SuspiciousEmptiness) as exc:
        publish.check_not_suspiciously_empty(5, _historial(*[1800] * 8))

    mensaje = str(exc.value)
    assert "fallo de fuente" in mensaje
    assert "No se sobrescribe" in mensaje


def test_aborts_on_a_severe_but_not_total_drop():
    """No hace falta llegar a cero: una caída del 95 % ya es sospechosa."""
    with pytest.raises(SuspiciousEmptiness):
        publish.check_not_suspiciously_empty(100, _historial(*[2000] * 12))


def test_allows_a_normal_daily_fluctuation():
    """De 2000 a 1400 es un martes de julio, no una avería."""
    publish.check_not_suspiciously_empty(1400, _historial(*[2000] * 12))


def test_allows_a_drop_just_above_the_threshold():
    mediana = 1000
    justo_encima = int(mediana * (1 - EMPTINESS_DROP_RATIO)) + 1

    publish.check_not_suspiciously_empty(justo_encima, _historial(*[mediana] * 6))


def test_uses_median_not_mean():
    """Una ejecución fallida a 0 no puede desactivar la guarda.

    Con media, un solo 0 en el histórico arrastra el umbral hacia abajo justo
    cuando la fuente está fallando y más falta hace la protección.
    """
    historial = _historial(2000, 2000, 2000, 0, 2000, 2000)

    with pytest.raises(SuspiciousEmptiness):
        publish.check_not_suspiciously_empty(0, historial)


def test_skips_the_check_without_enough_history():
    """Primera ejecución del repo: no hay con qué comparar, se publica."""
    publish.check_not_suspiciously_empty(0, [])


def test_skips_the_check_when_the_median_is_genuinely_low():
    """Febrero de madrugada: 3 hotspots en toda España es real.

    Aplicar la guarda con una mediana baja produciría abortos constantes fuera
    de temporada y acabaría con alguien desactivándola.
    """
    publish.check_not_suspiciously_empty(0, _historial(*[MIN_MEDIAN_FOR_CHECK - 5] * 10))


def test_ignores_malformed_history_entries():
    historial = [{"sin_hotspots": 1}, *_historial(*[2000] * 6)]

    with pytest.raises(SuspiciousEmptiness):
        publish.check_not_suspiciously_empty(0, historial)


# --- histórico de ejecuciones ------------------------------------------------


def test_history_roundtrip(tmp_path):
    destino = tmp_path / "runs.json"
    stats = RunStats.now(hotspots=1500, incidents=42)

    publish.save_history(destino, [stats.__dict__])

    assert publish.load_history(destino)[0]["hotspots"] == 1500


def test_history_is_capped_to_the_window(tmp_path):
    destino = tmp_path / "runs.json"

    publish.save_history(destino, _historial(*range(100)))

    assert len(publish.load_history(destino)) == publish.HISTORY_WINDOW


def test_history_keeps_the_most_recent_entries(tmp_path):
    destino = tmp_path / "runs.json"

    publish.save_history(destino, _historial(*range(100)))

    guardado = [r["hotspots"] for r in publish.load_history(destino)]
    assert guardado[-1] == 99


def test_corrupt_history_does_not_block_publication(tmp_path, caplog):
    """Un fichero de estado roto no puede convertirse en una caída del sistema."""
    destino = tmp_path / "runs.json"
    destino.write_text("{esto no es json", encoding="utf-8")

    with caplog.at_level("WARNING"):
        historial = publish.load_history(destino)

    assert historial == []
    assert any("ilegible" in r.getMessage() for r in caplog.records)


def test_missing_history_file_is_not_an_error(tmp_path):
    assert publish.load_history(tmp_path / "no-existe.json") == []


# --- orden de publicación ----------------------------------------------------


def test_publishes_in_the_declared_order():
    ejecutados = []
    pasos = [
        ("datos", lambda: ejecutados.append("datos")),
        ("sources.json", lambda: ejecutados.append("sources.json")),
        ("manifest.json", lambda: ejecutados.append("manifest.json")),
    ]

    publish.publish_atomically(pasos)

    assert ejecutados == ["datos", "sources.json", "manifest.json"]


def test_manifest_is_not_written_when_an_earlier_step_fails():
    """La regla de atomicidad de la sección 3.1.

    Si los datos fallan y el manifiesto se escribiese igual, el frontend leería
    un manifiesto fresco apuntando a ficheros que no están: mostraría cifras
    nuevas sobre datos viejos, que es exactamente el fallo que este proyecto
    existe para no cometer.
    """
    escritos = []

    def datos_rotos():
        raise OSError("disco lleno")

    pasos = [
        ("datos", datos_rotos),
        ("manifest.json", lambda: escritos.append("manifest.json")),
    ]

    with pytest.raises(OSError):
        publish.publish_atomically(pasos)

    assert escritos == []


def test_abort_names_the_failing_step(caplog):
    def roto():
        raise ValueError("boom")

    with caplog.at_level("ERROR"):
        with pytest.raises(ValueError):
            publish.publish_atomically([("sources.json", roto)])

    mensaje = " ".join(r.getMessage() for r in caplog.records)
    assert "sources.json" in mensaje
    assert "anteriores" in mensaje


def test_run_stats_are_serialisable():
    stats = RunStats.now(hotspots=10, incidents=2)

    payload = json.loads(json.dumps(stats.__dict__))

    assert payload["hotspots"] == 10
    assert payload["at"].endswith("Z")
