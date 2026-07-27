"""Ingesta desde NASA FIRMS.

FIRMS devuelve CSV plano. El esquema difiere entre VIIRS y MODIS (bright_ti4 vs
brightness, confidence categórico vs numérico), así que se normaliza a un único
contrato antes de salir de este módulo.
"""

from __future__ import annotations

import io
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import httpx
import pandas as pd

from .config import AREAS, DAY_RANGE, FIRMS_BASE, FIRMS_MAP_KEY, FIRMS_SOURCES, RAW

log = logging.getLogger(__name__)

SCHEMA = [
    "latitude",
    "longitude",
    "acq_dt",
    "satellite",
    "instrument",
    "source",
    "area_key",
    "confidence_raw",
    "confidence_pct",
    "brightness_k",
    "frp_mw",
    "daynight",
    "scan",
    "track",
]

_CONF_MAP = {"l": 20, "n": 60, "h": 90, "low": 20, "nominal": 60, "high": 90}


def _url(source: str, area: str) -> str:
    return f"{FIRMS_BASE}/{FIRMS_MAP_KEY}/{source}/{area}/{DAY_RANGE}"


def _fetch_one(client: httpx.Client, source: str, area_key: str, area: str) -> pd.DataFrame:
    url = _url(source, area)
    try:
        resp = client.get(url, timeout=60.0)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("FIRMS %s/%s falló: %s", source, area_key, exc)
        return pd.DataFrame()

    text = resp.text.strip()
    # FIRMS devuelve 200 con un cuerpo de texto plano cuando hay error de clave
    # o de cuota. Detectarlo por contenido, no por status.
    if not text or "," not in text.splitlines()[0]:
        log.warning("FIRMS %s/%s respuesta no-CSV: %s", source, area_key, text[:160])
        return pd.DataFrame()

    df = pd.read_csv(io.StringIO(text))
    if df.empty:
        return df

    df["source"] = source
    df["area_key"] = area_key
    return df


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=SCHEMA)

    out = pd.DataFrame(index=df.index)
    out["latitude"] = df["latitude"].astype(float)
    out["longitude"] = df["longitude"].astype(float)

    # acq_time viene como entero HHMM sin ceros a la izquierda: 45 -> 00:45.
    hhmm = df["acq_time"].astype(int).astype(str).str.zfill(4)
    out["acq_dt"] = pd.to_datetime(
        df["acq_date"].astype(str) + hhmm, format="%Y-%m-%d%H%M", utc=True
    )

    out["satellite"] = df["satellite"].astype(str)
    out["instrument"] = df["instrument"].astype(str)
    out["source"] = df["source"]
    out["area_key"] = df["area_key"]

    conf = df["confidence"]
    out["confidence_raw"] = conf.astype(str)
    if pd.api.types.is_numeric_dtype(conf):
        out["confidence_pct"] = conf.astype(float)
    else:
        out["confidence_pct"] = (
            conf.astype(str).str.strip().str.lower().map(_CONF_MAP).astype(float)
        )

    # VIIRS: bright_ti4 (canal I4, 3.74 um). MODIS: brightness (canal 21/22).
    out["brightness_k"] = (
        df["bright_ti4"] if "bright_ti4" in df.columns else df.get("brightness")
    ).astype(float)

    out["frp_mw"] = pd.to_numeric(df.get("frp"), errors="coerce").fillna(0.0)
    out["daynight"] = df.get("daynight", "").astype(str)
    out["scan"] = pd.to_numeric(df.get("scan"), errors="coerce")
    out["track"] = pd.to_numeric(df.get("track"), errors="coerce")

    return out[SCHEMA]


def fetch_hotspots(persist_raw: bool = True) -> pd.DataFrame:
    """Descarga todas las combinaciones sensor x área y devuelve un DataFrame único."""
    if not FIRMS_MAP_KEY:
        raise RuntimeError(
            "Falta FIRMS_MAP_KEY. Pídela gratis en "
            "https://firms.modaps.eosdis.nasa.gov/api/map_key/"
        )

    jobs = [(s, k, a) for s in FIRMS_SOURCES for k, a in AREAS.items()]
    frames: list[pd.DataFrame] = []

    with httpx.Client(follow_redirects=True) as client:
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(_fetch_one, client, s, k, a) for s, k, a in jobs]
            for fut in futures:
                raw = fut.result()
                if not raw.empty:
                    frames.append(_normalize(raw))

    if not frames:
        log.error("Ninguna petición a FIRMS devolvió datos")
        return pd.DataFrame(columns=SCHEMA)

    df = pd.concat(frames, ignore_index=True)

    # Deduplicación exacta: el mismo píxel puede llegar por dos áreas solapadas.
    df = df.drop_duplicates(subset=["latitude", "longitude", "acq_dt", "source"])
    df = df.sort_values("acq_dt").reset_index(drop=True)

    if persist_raw:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = RAW / f"firms_{stamp}.parquet"
        df.to_parquet(path, index=False)
        log.info("Raw guardado en %s (%d filas)", path, len(df))

    log.info("FIRMS: %d hotspots crudos", len(df))
    return df
