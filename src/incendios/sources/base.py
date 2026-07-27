"""Contrato común para las fuentes oficiales autonómicas.

Cada comunidad publica sus incendios en un formato distinto y ninguno es
estable. La estrategia es aislar la fragilidad: un adaptador por fuente, todos
detrás del mismo contrato, y un fallo en uno no tumba al resto.

El campo que hace que esto funcione es `precision_m`. INFOCAM publica el
centroide del municipio (error de kilómetros); 112 CV publica coordenadas del
incidente (error de decenas de metros). Fusionar ambos con la misma tolerancia
produce basura: o duplicas incendios o fusionas incendios distintos.
"""

from __future__ import annotations

import abc
import logging
from dataclasses import dataclass, field
from datetime import datetime

import httpx
import pandas as pd

log = logging.getLogger(__name__)

# Contrato de salida. Todo adaptador devuelve exactamente estas columnas.
OFFICIAL_SCHEMA = [
    "source_id",      # 'jcyl', 'infoca', ...
    "external_id",    # identificador en el sistema de origen
    "latitude",
    "longitude",
    "precision_m",    # error posicional declarado por la fuente
    "reported_at",    # UTC
    "status",         # activo | estabilizado | controlado | extinguido
    "municipio",
    "provincia",
    "level",          # nivel IGR / situación operativa, si la fuente lo da
    "resources",      # texto libre: medios actuando
    "raw_status",     # estado sin normalizar, para depurar cambios de formato
]

# Vocabulario normalizado. Cada adaptador mapea el suyo contra este.
STATUS_ACTIVE = "activo"
STATUS_STABILIZED = "estabilizado"
STATUS_CONTROLLED = "controlado"
STATUS_EXTINGUISHED = "extinguido"
STATUS_UNKNOWN = "desconocido"

VALID_STATUS = {
    STATUS_ACTIVE,
    STATUS_STABILIZED,
    STATUS_CONTROLLED,
    STATUS_EXTINGUISHED,
    STATUS_UNKNOWN,
}


@dataclass(frozen=True)
class SourceMeta:
    """Metadatos declarativos de una fuente.

    `precision_m` y `ttl_seconds` no son documentación: los consume el motor de
    fusión y el planificador de refresco.
    """

    source_id: str
    name: str
    region: str
    url: str
    precision_m: float
    ttl_seconds: int = 300
    attribution: str = ""
    notes: str = ""
    headers: dict[str, str] = field(default_factory=dict)


class OfficialSource(abc.ABC):
    """Adaptador de una fuente oficial.

    Implementar tres cosas: `meta`, `fetch_raw` y `parse`. El resto —reintentos,
    validación de esquema, aislamiento de errores— lo da la clase base.
    """

    @property
    @abc.abstractmethod
    def meta(self) -> SourceMeta: ...

    @abc.abstractmethod
    def fetch_raw(self, client: httpx.Client): ...

    @abc.abstractmethod
    def parse(self, raw) -> pd.DataFrame:
        """Devuelve un DataFrame con al menos las columnas obligatorias."""

    # --- infraestructura común ------------------------------------------

    def collect(self, client: httpx.Client) -> pd.DataFrame:
        m = self.meta
        try:
            raw = self.fetch_raw(client)
            df = self.parse(raw)
        except Exception as exc:  # noqa: BLE001 — aislamiento deliberado
            # Una fuente caída no puede tumbar el pipeline. En agosto, la web
            # de alguna comunidad SIEMPRE está caída.
            log.error("Fuente %s falló: %s: %s", m.source_id, type(exc).__name__, exc)
            return self.empty()

        if df.empty:
            log.info("Fuente %s: sin incendios", m.source_id)
            return self.empty()

        return self._finalize(df)

    def _finalize(self, df: pd.DataFrame) -> pd.DataFrame:
        m = self.meta
        df = df.copy()
        df["source_id"] = m.source_id

        for col in OFFICIAL_SCHEMA:
            if col not in df.columns:
                df[col] = None

        df["precision_m"] = df["precision_m"].fillna(m.precision_m)
        df["reported_at"] = pd.to_datetime(df["reported_at"], utc=True, errors="coerce")
        df["reported_at"] = df["reported_at"].fillna(pd.Timestamp.now(tz="UTC"))

        bad = ~df["status"].isin(VALID_STATUS)
        if bad.any():
            log.warning(
                "Fuente %s: %d filas con estado no reconocido %s",
                m.source_id, int(bad.sum()), df.loc[bad, "raw_status"].unique()[:5],
            )
            df.loc[bad, "status"] = STATUS_UNKNOWN

        coords_ok = df["latitude"].between(27, 44) & df["longitude"].between(-19, 5)
        if (~coords_ok).any():
            log.warning(
                "Fuente %s: %d filas fuera de España descartadas",
                m.source_id, int((~coords_ok).sum()),
            )
        df = df[coords_ok]

        log.info("Fuente %s: %d incendios", m.source_id, len(df))
        return df[OFFICIAL_SCHEMA]

    @staticmethod
    def empty() -> pd.DataFrame:
        return pd.DataFrame(columns=OFFICIAL_SCHEMA)

    @staticmethod
    def norm_status(value: str, mapping: dict[str, str]) -> str:
        """Normaliza un estado buscando subcadenas, no igualdad exacta.

        Las fuentes escriben 'Activo', 'ACTIVO', 'Nivel 1 - Activo'... Buscar
        subcadena sobre texto en minúsculas aguanta esas variaciones sin tener
        que enumerarlas todas.
        """
        if not value:
            return STATUS_UNKNOWN
        low = str(value).strip().lower()
        for needle, norm in mapping.items():
            if needle in low:
                return norm
        return STATUS_UNKNOWN
