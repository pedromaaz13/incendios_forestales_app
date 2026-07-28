"""Estado de salud por fuente · RF-P-10.

`sources.json` es lo que permite al frontend decir "INFOCAM lleva 40 minutos sin
responder" en lugar de "Castilla-La Mancha: 0 incendios". La diferencia entre
esas dos frases es todo el proyecto.

El estado se deriva de dos cosas y solo dos: si el último intento tuvo éxito, y
cuánto hace del último éxito comparado con el TTL declarado por la fuente.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd

log = logging.getLogger(__name__)

STATUS_OK = "ok"
STATUS_STALE = "stale"
STATUS_ERROR = "error"
STATUS_DISABLED = "disabled"

# Múltiplo del TTL a partir del cual un éxito se considera rancio. x3 da margen
# a dos fallos transitorios seguidos sin alarmar.
STALE_TTL_MULTIPLIER = 3

# Umbral de degradación global de RF-P-10: 4 h.
MAX_DATA_AGE_SECONDS = 14400


@dataclass
class SourceHealth:
    """Estado de una fuente en una ejecución. Se serializa tal cual al bloque 4.2."""

    id: str
    name: str
    region: str
    kind: str  # oficial | satelite | contexto
    critical: bool = False
    ttl_seconds: int = 300
    precision_m: float | None = None
    configured: bool = True
    last_success_at: datetime | None = None
    records: int = 0
    error: str | None = None
    consecutive_failures: int = 0
    attribution: str = ""

    def age_seconds(self, now: datetime) -> int | None:
        if self.last_success_at is None:
            return None
        return max(0, int((now - self.last_success_at).total_seconds()))

    def status(self, now: datetime) -> str:
        if not self.configured:
            return STATUS_DISABLED
        if self.error is not None:
            return STATUS_ERROR
        edad = self.age_seconds(now)
        if edad is None:
            return STATUS_ERROR
        if edad > self.ttl_seconds * STALE_TTL_MULTIPLIER:
            return STATUS_STALE
        return STATUS_OK

    def to_dict(self, now: datetime) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "region": self.region,
            "kind": self.kind,
            "critical": self.critical,
            "status": self.status(now),
            "last_success_at": _iso(self.last_success_at),
            "age_seconds": self.age_seconds(now),
            "ttl_seconds": self.ttl_seconds,
            "records": int(self.records),
            "precision_m": self.precision_m,
            "error": self.error,
            "consecutive_failures": int(self.consecutive_failures),
            "attribution": self.attribution,
        }


@dataclass
class HealthReport:
    sources: list[SourceHealth] = field(default_factory=list)

    def add(self, source: SourceHealth) -> None:
        self.sources.append(source)

    def degraded(self, now: datetime, worst_data_age_seconds: int | None = None) -> tuple[bool, str | None]:
        """`degraded` si una fuente crítica falla o si el dato es demasiado viejo.

        Devuelve también el motivo: una banda ámbar que no dice qué pasa obliga
        al usuario a adivinar, y adivinar es lo que este proyecto evita.
        """
        motivos: list[str] = []

        rotas = [
            s for s in self.sources
            if s.critical and s.status(now) in (STATUS_ERROR, STATUS_STALE)
        ]
        if rotas:
            motivos.append(
                "fuentes críticas sin datos recientes: "
                + ", ".join(f"{s.name} ({s.status(now)})" for s in rotas)
            )

        if worst_data_age_seconds is not None and worst_data_age_seconds > MAX_DATA_AGE_SECONDS:
            horas = worst_data_age_seconds / 3600
            motivos.append(f"el dato más antiguo tiene {horas:.1f} h (umbral: 4 h)")

        if not motivos:
            return False, None
        return True, "; ".join(motivos)

    def to_dict(self, now: datetime) -> dict:
        # Las fuentes en error van arriba: es lo que RF-F-06 pinta primero y lo
        # que alguien mirando el panel necesita ver sin desplazar.
        orden = {STATUS_ERROR: 0, STATUS_STALE: 1, STATUS_DISABLED: 2, STATUS_OK: 3}
        ordenadas = sorted(self.sources, key=lambda s: (orden[s.status(now)], s.name))
        return {
            "generated_at": _iso(now),
            "sources": [s.to_dict(now) for s in ordenadas],
        }

    def write(self, path, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        payload = self.to_dict(now)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        cuenta = {}
        for s in payload["sources"]:
            cuenta[s["status"]] = cuenta.get(s["status"], 0) + 1
        log.info("sources.json -> %s", cuenta)
        return payload


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        value = value.to_pydatetime()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def from_official_sources(registry, results: dict[str, pd.DataFrame], now: datetime | None = None) -> list[SourceHealth]:
    """Construye el estado de las fuentes autonómicas a partir del registro.

    Una fuente sin URL configurada sale como `disabled`, no como `error`: no ha
    fallado, es que todavía no existe. Mezclarlas haría que el panel pareciera
    roto cuando solo está incompleto.
    """
    now = now or datetime.now(timezone.utc)
    salida: list[SourceHealth] = []

    for src in registry:
        meta = src.meta
        configurada = bool(meta.url)
        df = results.get(meta.source_id)
        exito = configurada and df is not None and not df.empty

        salida.append(
            SourceHealth(
                id=meta.source_id,
                name=meta.name,
                region=meta.region,
                kind="oficial",
                critical=False,
                ttl_seconds=meta.ttl_seconds,
                precision_m=meta.precision_m,
                configured=configurada,
                last_success_at=now if exito else None,
                records=0 if df is None else len(df),
                error=None if configurada else "endpoint sin descubrir",
                attribution=meta.attribution,
            )
        )
    return salida
