"""Fusión de incendios oficiales con detecciones satelitales.

El problema: el mismo incendio llega por dos vías con propiedades opuestas.

  Oficial  → nombre, estado, medios actuando, confirmación humana.
             Posición a veces al centroide del municipio (error de km).
  FIRMS    → posición precisa (375 m), FRP, extensión real.
             Sin nombre, sin estado, sin confirmación.

Fusionarlos bien da lo que ninguna de las dos fuentes da sola: un incendio
confirmado, localizado con precisión y con intensidad medida.

La regla central: **la tolerancia de emparejamiento la fija la fuente menos
precisa**. Emparejar un punto de INFOCAM (centroide municipal, ±6 km) con una
tolerancia de 500 m no encuentra nada; emparejar uno de 112 CV (±100 m) con
tolerancia de 6 km fusiona incendios vecinos distintos.
"""

from __future__ import annotations

import logging

import geopandas as gpd
import numpy as np
import pandas as pd

from .config import CRS_METRIC_CANARIAS, CRS_METRIC_MAINLAND, CRS_WGS84

log = logging.getLogger(__name__)

# Margen sobre la precisión declarada. Un incendio se propaga: el frente puede
# estar a kilómetros del punto que notificó la comunidad hace 6 horas.
MATCH_SLACK_M = 3000.0
MATCH_MAX_M = 15000.0

# Ventana temporal. Un incendio oficial notificado hace 3 días no debe
# emparejarse con una detección satelital de esta mañana en el mismo valle.
MATCH_WINDOW_HOURS = 48.0


def _metric_crs(lon: float) -> int:
    return CRS_METRIC_CANARIAS if lon < -12 else CRS_METRIC_MAINLAND


def _tolerance_m(precision_m: pd.Series) -> pd.Series:
    return (precision_m.astype(float) + MATCH_SLACK_M).clip(upper=MATCH_MAX_M)


def match(
    official: pd.DataFrame,
    fires: gpd.GeoDataFrame,
) -> tuple[pd.DataFrame, gpd.GeoDataFrame]:
    """Empareja incendios oficiales con clusters FIRMS.

    Devuelve `(official_enriquecido, fires_enriquecido)`. No fusiona filas: cada
    lado conserva su identidad y gana referencias cruzadas. Así el frontend puede
    mostrar "confirmado por INFOCA" sin perder el FRP, y un incendio oficial sin
    detección satelital sigue apareciendo (puede ser pequeño, o de noche bajo
    nubes, y sigue siendo real).
    """
    official = official.copy()
    official["fire_id"] = None
    official["match_distance_m"] = np.nan

    fires = fires.copy()
    fires["confirmed_by"] = ""
    fires["official_name"] = None
    fires["official_status"] = None

    if official.empty or fires.empty:
        log.info("Fusión omitida: official=%d fires=%d", len(official), len(fires))
        return official, fires

    crs = _metric_crs(float(fires.geometry.x.mean()))

    off_gdf = gpd.GeoDataFrame(
        official,
        geometry=gpd.points_from_xy(official["longitude"], official["latitude"]),
        crs=CRS_WGS84,
    ).to_crs(crs)
    fire_gdf = fires.to_crs(crs)

    off_gdf["_tol"] = _tolerance_m(off_gdf["precision_m"])

    # sjoin_nearest con la tolerancia máxima, y después se filtra por la
    # tolerancia real de cada fila. Un solo pase espacial en lugar de N.
    joined = gpd.sjoin_nearest(
        off_gdf,
        fire_gdf[["fire_id", "last_detected", "first_detected", "geometry"]].rename(
            columns={"fire_id": "_cand_fire_id"}
        ),
        how="left",
        max_distance=MATCH_MAX_M,
        distance_col="_dist",
    )
    joined = joined[~joined.index.duplicated(keep="first")]

    within_tol = joined["_dist"] <= joined["_tol"]

    dt_hours = (
        (joined["reported_at"] - joined["last_detected"]).dt.total_seconds().abs() / 3600.0
    )
    within_time = dt_hours <= MATCH_WINDOW_HOURS

    ok = within_tol & within_time & joined["_cand_fire_id"].notna()

    official.loc[ok.values, "fire_id"] = joined.loc[ok, "_cand_fire_id"].values
    official.loc[ok.values, "match_distance_m"] = joined.loc[ok, "_dist"].round(0).values

    # Propagación inversa: un cluster puede quedar confirmado por varias fuentes.
    matched = official[official["fire_id"].notna()]
    if not matched.empty:
        by_fire = matched.groupby("fire_id").agg(
            confirmed_by=("source_id", lambda s: ",".join(sorted(set(s)))),
            official_name=("municipio", "first"),
            official_status=("status", _worst_status),
        )
        fires = fires.set_index("fire_id")
        fires.update(by_fire)
        fires = fires.reset_index()

    log.info(
        "Fusión: %d/%d oficiales emparejados · %d clusters confirmados",
        int(ok.sum()), len(official), int(fires["confirmed_by"].astype(bool).sum()),
    )
    return official, fires


_STATUS_RANK = {
    "activo": 4,
    "estabilizado": 3,
    "controlado": 2,
    "extinguido": 1,
    "desconocido": 0,
}


def _worst_status(values: pd.Series) -> str:
    """Ante fuentes discrepantes, gana el estado más grave.

    Si INFOCA dice 'controlado' y JCyL dice 'activo' para el mismo frente,
    mostrar 'controlado' es el error caro. Se elige siempre el peor caso.
    """
    return max(values, key=lambda v: _STATUS_RANK.get(v, 0))


def build_incidents(official: pd.DataFrame, fires: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Capa unificada para el frontend: un incidente por incendio real.

    Prioridad de posición: si hay emparejamiento, gana la del cluster FIRMS
    (más precisa). Si no, la oficial. Un incendio oficial huérfano se marca como
    `satellite_confirmed=False` y el frontend debe dibujarlo distinto: no es
    menos real, es menos localizado.
    """
    fires = fires.copy()
    fires["origin"] = np.where(fires["confirmed_by"].astype(bool), "ambos", "satelite")
    fires["satellite_confirmed"] = True

    orphans = official[official["fire_id"].isna()].copy()
    if not orphans.empty:
        orphans = gpd.GeoDataFrame(
            orphans,
            geometry=gpd.points_from_xy(orphans["longitude"], orphans["latitude"]),
            crs=CRS_WGS84,
        )
        orphans["origin"] = "oficial"
        orphans["satellite_confirmed"] = False
        orphans["fire_id"] = "off_" + orphans["source_id"] + "_" + orphans["external_id"].astype(str)
        orphans["confirmed_by"] = orphans["source_id"]
        orphans["official_status"] = orphans["status"]
        orphans["status"] = "activo"

    cols = [
        "fire_id", "origin", "satellite_confirmed", "confirmed_by",
        "official_status", "municipio", "provincia", "precision_m", "geometry",
    ]
    parts = [fires.reindex(columns=[c for c in fires.columns])]
    if not orphans.empty:
        parts.append(orphans)

    out = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs=CRS_WGS84)
    for c in cols:
        if c not in out.columns:
            out[c] = None

    log.info(
        "Incidentes: %d satelitales · %d oficiales huérfanos",
        len(fires), len(orphans) if not orphans.empty else 0,
    )
    return out
