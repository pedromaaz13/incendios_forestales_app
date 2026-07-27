"""Publicación atómica y guardas previas · RF-P-11.

Dos guardas, y las dos existen para no publicar una mentira tranquilizadora.

**Orden estricto.** Primero los datos, luego `sources.json`, y `manifest.json`
el último. Si la ejecución muere a mitad, el manifiesto no se actualiza y el
frontend sigue leyendo el anterior, cuya edad crece a la vista. Un manifiesto
fresco apuntando a datos que no están es la única salida peor que no publicar.

**Vaciado sospechoso.** Si FIRMS devuelve 0 hotspots y las últimas ejecuciones
traían cientos, no es que se hayan apagado todos los incendios de España: es que
la fuente falló. Se aborta sin sobrescribir.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

# Caída porcentual respecto a la mediana reciente que dispara el aborto.
EMPTINESS_DROP_RATIO = 0.90

# Ejecuciones que se guardan para calcular la mediana.
HISTORY_WINDOW = 24

# Por debajo de este número la mediana no es fiable: en febrero de madrugada
# puede haber 3 hotspots en toda España de forma legítima.
MIN_MEDIAN_FOR_CHECK = 20


class SuspiciousEmptiness(SystemExit):
    """Aborto por salida sospechosamente vacía. Hereda de SystemExit a propósito:
    el objetivo es que Actions marque la ejecución en rojo y no corra el sync."""


@dataclass
class RunStats:
    """Contadores de una ejecución, para comparar con las anteriores."""

    hotspots: int
    incidents: int
    at: str

    @classmethod
    def now(cls, hotspots: int, incidents: int) -> RunStats:
        return cls(
            hotspots=int(hotspots),
            incidents=int(incidents),
            at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )


def load_history(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        # Un histórico corrupto no puede impedir publicar: se descarta y se
        # reconstruye. Lo contrario convertiría un fichero roto en una caída.
        log.warning("Histórico de ejecuciones ilegible (%s); se reinicia", exc)
        return []


def save_history(path: Path, historial: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(historial[-HISTORY_WINDOW:], indent=2), encoding="utf-8"
    )


def _median(valores: list[int]) -> float:
    if not valores:
        return 0.0
    ordenados = sorted(valores)
    mitad = len(ordenados) // 2
    if len(ordenados) % 2:
        return float(ordenados[mitad])
    return (ordenados[mitad - 1] + ordenados[mitad]) / 2.0


def check_not_suspiciously_empty(hotspots: int, historial: list[dict]) -> None:
    """Aborta si el recuento se desploma respecto a la mediana reciente.

    La mediana y no la media: una sola ejecución fallida a 0 arrastraría la media
    hacia abajo y desactivaría la guarda justo cuando más falta hace.
    """
    previos = [int(r["hotspots"]) for r in historial if "hotspots" in r]
    mediana = _median(previos)

    if mediana < MIN_MEDIAN_FOR_CHECK:
        log.info(
            "Guarda de vaciado omitida: mediana reciente %.0f < %d, sin base para comparar",
            mediana, MIN_MEDIAN_FOR_CHECK,
        )
        return

    umbral = mediana * (1 - EMPTINESS_DROP_RATIO)
    if hotspots <= umbral:
        raise SuspiciousEmptiness(
            f"Vaciado sospechoso: {hotspots} hotspots frente a una mediana de "
            f"{mediana:.0f} en las últimas {len(previos)} ejecuciones "
            f"(caída > {EMPTINESS_DROP_RATIO:.0%}). Es un fallo de fuente, no "
            "ausencia de incendios. No se sobrescribe nada."
        )

    log.info(
        "Guarda de vaciado: %d hotspots, mediana reciente %.0f. Correcto",
        hotspots, mediana,
    )


def publish_atomically(steps: list[tuple[str, callable]]) -> None:
    """Ejecuta los pasos en orden y aborta al primer fallo.

    `manifest.json` va siempre el último de la lista. Es lo único que hace
    atómica la publicación desde el punto de vista del frontend: mientras el
    manifiesto no cambie, el visor considera que la ejecución anterior sigue
    siendo la buena.
    """
    for nombre, paso in steps:
        try:
            paso()
        except Exception as exc:
            log.error(
                "Publicación abortada en '%s': %s: %s. Los artefactos anteriores "
                "quedan intactos y el frontend seguirá mostrando su edad real",
                nombre, type(exc).__name__, exc,
            )
            raise
        log.info("Publicado: %s", nombre)
