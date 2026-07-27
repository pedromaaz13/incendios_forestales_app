"""Agrupa hotspots en incendios y deriva perímetro y métricas.

Un hotspot no es un incendio: es un píxel de 375 m con anomalía térmica. Un
incendio de 500 ha genera decenas de hotspots por pasada y varias pasadas al
día. Sin este paso, la web muestra ruido.

Se implementa ST-DBSCAN mediante un truco: se proyecta a metros, se convierte el
tiempo a "metros equivalentes" con un factor de escala, y se corre un DBSCAN
euclídeo en 3D. Es equivalente a ST-DBSCAN con distancia combinada y evita una
implementación propia.
"""

from __future__ import annotations

import hashlib
import logging

import geopandas as gpd
import numpy as np
import pandas as pd
from sklearn.cluster import DBSCAN

from .config import (
    ACTIVE_WINDOW_HOURS,
    CLUSTER,
    CRS_METRIC_CANARIAS,
    CRS_METRIC_MAINLAND,
    CRS_WGS84,
    HULL_RATIO,
)

log = logging.getLogger(__name__)


def _metric_crs(area_key: str) -> int:
    return CRS_METRIC_CANARIAS if area_key == "canarias" else CRS_METRIC_MAINLAND


def _cluster_block(gdf: gpd.GeoDataFrame, crs: int) -> np.ndarray:
    p = gdf.to_crs(crs)
    t0 = p["acq_dt"].min()
    hours = (p["acq_dt"] - t0).dt.total_seconds() / 3600.0

    X = np.column_stack(
        [p.geometry.x, p.geometry.y, hours * CLUSTER.time_scale_m_per_hour]
    )
    return DBSCAN(
        eps=CLUSTER.eps_m, min_samples=CLUSTER.min_samples, n_jobs=-1
    ).fit_predict(X)


def assign_fire_ids(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Etiqueta cada hotspot con un fire_id estable.

    El id se deriva de un hash del centroide redondeado + fecha de inicio, no de
    un autoincremental: así un mismo incendio conserva su id entre ejecuciones
    aunque cambie el orden de las filas.
    """
    out = []
    for area_key, block in gdf.groupby("area_key", sort=False):
        labels = _cluster_block(block, _metric_crs(str(area_key)))
        block = block.copy()
        block["_label"] = [f"{area_key}:{lab}" for lab in labels]
        out.append(block)

    gdf = pd.concat(out, ignore_index=True)
    gdf = gdf[~gdf["_label"].str.endswith(":-1")].copy()

    seeds = gdf.groupby("_label").agg(
        lat=("latitude", "mean"),
        lon=("longitude", "mean"),
        start=("acq_dt", "min"),
    )
    seeds["fire_id"] = [
        hashlib.sha1(
            f"{r.lat:.3f}|{r.lon:.3f}|{r.start:%Y-%m-%d}".encode()
        ).hexdigest()[:10]
        for r in seeds.itertuples()
    ]

    gdf["fire_id"] = gdf["_label"].map(seeds["fire_id"])
    gdf = gdf.drop(columns=["_label"])

    log.info("Clustering: %d hotspots -> %d incendios", len(gdf), gdf["fire_id"].nunique())
    return gdf


def build_fires(gdf: gpd.GeoDataFrame, now: pd.Timestamp | None = None) -> gpd.GeoDataFrame:
    """Una fila por incendio, con geometría de centroide y métricas agregadas."""
    now = now or pd.Timestamp.now(tz="UTC")

    agg = gdf.groupby("fire_id").agg(
        area_key=("area_key", "first"),
        n_hotspots=("fire_id", "size"),
        frp_total_mw=("frp_mw", "sum"),
        frp_max_mw=("frp_mw", "max"),
        brightness_max_k=("brightness_k", "max"),
        confidence_mean=("confidence_pct", "mean"),
        first_detected=("acq_dt", "min"),
        last_detected=("acq_dt", "max"),
        lat=("latitude", "mean"),
        lon=("longitude", "mean"),
        sensors=("source", lambda s: ",".join(sorted(set(s)))),
    )

    agg["hours_since_last"] = (now - agg["last_detected"]).dt.total_seconds() / 3600.0
    agg["duration_hours"] = (
        agg["last_detected"] - agg["first_detected"]
    ).dt.total_seconds() / 3600.0
    agg["status"] = np.where(
        agg["hours_since_last"] <= ACTIVE_WINDOW_HOURS, "activo", "inactivo"
    )

    # Superficie afectada aproximada: cada hotspot VIIRS cubre ~0.14 km2. Es una
    # cota inferior grosera; se etiqueta como estimación en el frontend.
    agg["area_est_ha"] = (agg["n_hotspots"] * 14.06).round(0)

    # Escala de intensidad derivada del FRP total, no de un umbral arbitrario.
    agg["intensity"] = pd.cut(
        agg["frp_total_mw"],
        bins=[-np.inf, 50, 200, 800, np.inf],
        labels=["baja", "media", "alta", "extrema"],
    ).astype(str)

    fires = gpd.GeoDataFrame(
        agg.reset_index(),
        geometry=gpd.points_from_xy(agg["lon"].values, agg["lat"].values),
        crs=CRS_WGS84,
    )
    return fires


def build_perimeters(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Envolvente cóncava por incendio, bufferizada al tamaño del píxel.

    Un incendio de un solo hotspot no tiene hull: se representa como el propio
    píxel (círculo de ~190 m de radio, medio píxel VIIRS).
    """
    blocks: list[gpd.GeoDataFrame] = []

    for area_key, block in gdf.groupby("area_key", sort=False):
        crs = _metric_crs(str(area_key))
        p = block.to_crs(crs)

        rows = []
        for fire_id, grp in p.groupby("fire_id"):
            geom = grp.geometry.union_all()
            if len(grp) >= 3:
                try:
                    geom = geom.concave_hull(ratio=HULL_RATIO)
                except AttributeError:  # shapely < 2.1
                    geom = geom.convex_hull
            geom = geom.buffer(190)
            rows.append({"fire_id": fire_id, "hull_area_ha": geom.area / 10_000.0, "geometry": geom})

        if rows:
            blocks.append(gpd.GeoDataFrame(rows, crs=crs).to_crs(CRS_WGS84))

    if not blocks:
        return gpd.GeoDataFrame(
            {"fire_id": [], "hull_area_ha": []}, geometry=[], crs=CRS_WGS84
        )

    out = gpd.GeoDataFrame(pd.concat(blocks, ignore_index=True), crs=CRS_WGS84)
    out["hull_area_ha"] = out["hull_area_ha"].round(1)
    log.info("Perímetros generados: %d", len(out))
    return out
