"""Construcción de los artefactos de `live/` · secciones 3.1 y 4.

Este módulo es el que traduce las tablas del pipeline al contrato que lee el
navegador. Se separa de `export.py` a propósito: `export` escribe ficheros,
`build` decide qué va dentro de ellos.

La pieza con más carga es `build_manifest`. Publica **dos latencias distintas**
—la edad del dato satelital y la de la última ejecución— porque confundirlas es
el error de diseño que este proyecto existe para no cometer. Un dato de hace tres
horas presentado con la marca de tiempo de una ejecución de hace un minuto es
desinformación, aunque los dos números sean ciertos por separado.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import geopandas as gpd
import pandas as pd

from .merge import INCIDENT_SCHEMA

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

DISCLAIMER = (
    "Detecciones satelitales de anomalías térmicas y partes oficiales. No es "
    "información oficial de emergencias. Ante una emergencia, 112."
)

# Campos que viajan al navegador en incidents.geojson.
INCIDENT_WEB_FIELDS = INCIDENT_SCHEMA


def _iso(ts) -> str | None:
    if ts is None or (isinstance(ts, float) and pd.isna(ts)):
        return None
    ts = pd.Timestamp(ts)
    if pd.isna(ts):
        return None
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def data_age_seconds(hotspots: pd.DataFrame, official: pd.DataFrame | None, now: datetime) -> dict:
    """Edad del dato **por familia de fuente**, no un número único.

    VIIRS deja huecos de horas entre pasadas polares; SEVIRI cubre cada 15 min;
    los scrapers oficiales, cada pocos minutos. Promediarlos daría un número que
    no describe a ninguno.
    """
    ahora = pd.Timestamp(now)
    edades: dict[str, int] = {}

    if len(hotspots) and "acq_dt" in hotspots.columns:
        instrumento = (
            hotspots["instrument"].astype(str).str.upper()
            if "instrument" in hotspots.columns
            else pd.Series(["VIIRS"] * len(hotspots), index=hotspots.index)
        )
        for clave, mascara in (
            ("firms_viirs", instrumento.str.startswith("VIIRS")),
            ("firms_modis", instrumento.str.startswith("MODIS")),
            ("seviri", instrumento.str.startswith("SEVIRI")),
        ):
            bloque = hotspots.loc[mascara, "acq_dt"]
            if len(bloque):
                edades[clave] = max(0, int((ahora - bloque.max()).total_seconds()))

    if official is not None and len(official) and "reported_at" in official.columns:
        ultimo = pd.to_datetime(official["reported_at"], utc=True, errors="coerce").max()
        if pd.notna(ultimo):
            edades["official"] = max(0, int((ahora - ultimo).total_seconds()))

    return edades


def build_manifest(
    hotspots: gpd.GeoDataFrame,
    incidents: gpd.GeoDataFrame,
    *,
    official: pd.DataFrame | None = None,
    suppressed_industrial: int = 0,
    suppressed_lowconf: int = 0,
    pipeline_started_at: datetime | None = None,
    degraded: bool = False,
    degraded_reason: str | None = None,
    now: datetime | None = None,
) -> dict:
    """Manifiesto de la sección 4.1."""
    now = now or datetime.now(timezone.utc)

    edades = data_age_seconds(hotspots, official, now)
    peor = max(edades.values()) if edades else None

    pipeline_age = (
        int((now - pipeline_started_at).total_seconds())
        if pipeline_started_at is not None
        else 0
    )

    if len(incidents):
        sat = incidents["satellite_confirmed"].fillna(False).astype(bool)
        off = incidents["official_confirmed"].fillna(False).astype(bool)
        solo_oficial = int((off & ~sat).sum())
        confirmados = int(sat.sum())
        frp = float(pd.to_numeric(incidents.get("frp_total_mw"), errors="coerce").fillna(0).sum())
    else:
        solo_oficial = confirmados = 0
        frp = 0.0

    # `degraded` también se dispara por dato viejo aunque todas las fuentes
    # respondan: una fuente que contesta rápido con datos de hace 5 h sigue
    # siendo un mapa que no se puede usar para decidir nada.
    if peor is not None and peor > 14400 and not degraded:
        degraded = True
        degraded_reason = f"el dato más antiguo tiene {peor / 3600:.1f} h (umbral: 4 h)"

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _iso(now),
        "pipeline_age_seconds": pipeline_age,
        "data_age_seconds": edades,
        "worst_data_age_seconds": peor,
        "counts": {
            "incidents_total": int(len(incidents)),
            "incidents_satellite_confirmed": confirmados,
            "incidents_official_only": solo_oficial,
            "hotspots_24h": int(len(hotspots)),
            "hotspots_suppressed_industrial": int(suppressed_industrial),
            "hotspots_suppressed_lowconf": int(suppressed_lowconf),
        },
        "frp_total_mw": round(frp, 1),
        "degraded": bool(degraded),
        "degraded_reason": degraded_reason,
        "disclaimer": DISCLAIMER,
    }
    return manifest


def write_manifest(manifest: dict, path) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info(
        "manifest.json -> %d incidentes · dato %ss · ejecución %ss%s",
        manifest["counts"]["incidents_total"],
        manifest["worst_data_age_seconds"],
        manifest["pipeline_age_seconds"],
        " · DEGRADADO" if manifest["degraded"] else "",
    )
    return manifest


def incidents_for_web(incidents: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Recorta al contrato 4.3 y serializa fechas.

    Cada propiedad extra se multiplica por el número de features y el
    presupuesto de RNF-02 son 900 KB para toda la carga inicial.
    """
    out = incidents.copy()

    for col in INCIDENT_WEB_FIELDS:
        if col not in out.columns:
            out[col] = None

    for col in ("first_detected", "last_detected", "started_at"):
        out[col] = out[col].map(_iso)

    for col in ("igr_level", "resources_air", "resources_ground", "resources_people"):
        out[col] = pd.to_numeric(out[col], errors="coerce")

    return gpd.GeoDataFrame(
        out[[*INCIDENT_WEB_FIELDS, "geometry"]], geometry="geometry", crs=incidents.crs
    )
