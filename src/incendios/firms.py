"""Ingesta desde NASA FIRMS.

FIRMS devuelve CSV plano. El esquema difiere entre VIIRS y MODIS (bright_ti4 vs
brightness, confidence categórico vs numérico), así que se normaliza a un único
contrato antes de salir de este módulo.
"""

from __future__ import annotations

import io
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

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

# Columnas sin las que no se puede construir un hotspot. El brillo se valida
# aparte porque VIIRS y MODIS lo publican con nombres distintos.
REQUIRED_CSV_COLUMNS = (
    "latitude",
    "longitude",
    "acq_date",
    "acq_time",
    "satellite",
    "instrument",
    "confidence",
)


def _require_columns(df: pd.DataFrame) -> None:
    """Falla con el sensor, el área y el diff de columnas.

    Un `KeyError: 'latitude'` a las 4 de la mañana en plena temporada no dice
    qué combinación sensor/bbox rompió ni contra qué esquema. Esto sí.
    """
    faltan = [c for c in REQUIRED_CSV_COLUMNS if c not in df.columns]
    if "bright_ti4" not in df.columns and "brightness" not in df.columns:
        faltan.append("bright_ti4|brightness")

    if not faltan:
        return

    origen = df["source"].iloc[0] if "source" in df.columns and len(df) else "?"
    area = df["area_key"].iloc[0] if "area_key" in df.columns and len(df) else "?"
    raise ValueError(
        f"FIRMS {origen}/{area}: el CSV no trae las columnas obligatorias "
        f"{faltan}. Recibidas: {sorted(df.columns)}. "
        "¿Ha cambiado el esquema de FIRMS?"
    )


def _url(source: str, area: str) -> str:
    return f"{FIRMS_BASE}/{FIRMS_MAP_KEY}/{source}/{area}/{DAY_RANGE}"


# Reintentos ante fallo de transporte, con espera creciente.
#
# Medido en producción el 30-07-2026: 2 de 12 ejecuciones murieron con
# `Network is unreachable` en las 12 peticiones a la vez. No era FIRMS caído —la
# ejecución anterior y la siguiente funcionaron— sino la red del runner fallando
# unos segundos. Sin reintento, ese segundo cuesta media hora de datos, porque el
# cron no vuelve hasta la siguiente marca.
#
# Solo se reintentan los fallos de **transporte**. Una respuesta no-CSV es la
# clave agotada o inválida, y repetirla no la arregla: solo gastaría cuota.
INTENTOS = 3
ESPERA_BASE_S = 2.0

# Cuota restante de FIRMS.
#
# Agotarla se manifiesta como **cero incendios**, que es el fallo que este
# proyecto existe para no cometer. Con este número el panel de fuentes lo puede
# avisar antes de que pase.
#
# **FIRMS no lo manda en ninguna cabecera.** Se comprobó el 30-07-2026: las
# respuestas de la API de área solo traen `x-frame-options` y
# `x-content-type-options`. La primera versión de esto leía una cabecera
# `Remaining-request-endpoint` copiada de AEMET, y publicaba un campo que salía
# siempre nulo — un dato inventado por asumir en vez de mirar.
#
# El dato real está en un endpoint aparte, cuyo esquema es:
#
#     { "transaction_limit": 5000, "current_transactions": 54,
#       "transaction_interval": "10 minutes" }
#
# Es un módulo a nivel de proceso porque el pipeline corre una vez y muere.
QUOTA_URL = "https://firms.modaps.eosdis.nasa.gov/mapserver/mapkey_status/"

cuota_restante: int | None = None
cuota_limite: int | None = None


def consultar_cuota(client: httpx.Client | None = None) -> int | None:
    """Pregunta a FIRMS cuántas peticiones quedan en la ventana actual.

    Devuelve `None` si no se puede saber: sin clave, sin red, o si FIRMS cambia
    el esquema. Es telemetría, así que un fallo aquí no afecta a la ingesta.
    """
    global cuota_restante, cuota_limite

    if not FIRMS_MAP_KEY:
        return None

    propio = client is None
    client = client or httpx.Client(follow_redirects=True, timeout=30.0)
    try:
        r = client.get(QUOTA_URL, params={"MAP_KEY": FIRMS_MAP_KEY})
        r.raise_for_status()
        cuerpo = r.json()
        limite = int(cuerpo["transaction_limit"])
        usadas = int(cuerpo["current_transactions"])
    except Exception as exc:
        log.warning("FIRMS: no se pudo consultar la cuota (%s)", exc)
        return None
    finally:
        if propio:
            client.close()

    cuota_limite = limite
    cuota_restante = max(0, limite - usadas)

    # Se avisa antes de quedarse a cero, no cuando ya no hay datos. El umbral es
    # relativo porque el límite lo fija FIRMS y puede cambiar.
    nivel = log.warning if cuota_restante < limite * 0.1 else log.info
    nivel(
        "FIRMS: %d de %d peticiones usadas en la ventana de %s",
        usadas, limite, cuerpo.get("transaction_interval", "?"),
    )
    return cuota_restante


def _fetch_one(client: httpx.Client, source: str, area_key: str, area: str) -> pd.DataFrame:
    url = _url(source, area)

    for intento in range(1, INTENTOS + 1):
        try:
            resp = client.get(url, timeout=60.0)
            resp.raise_for_status()
            break
        except httpx.HTTPError as exc:
            ultimo = intento == INTENTOS
            log.warning(
                "FIRMS %s/%s falló (intento %d/%d): %s%s",
                source, area_key, intento, INTENTOS, exc,
                "" if ultimo else " · se reintenta",
            )
            if ultimo:
                return pd.DataFrame()
            time.sleep(ESPERA_BASE_S * 2 ** (intento - 1))

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

    _require_columns(df)

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
    brillo = df["bright_ti4"] if "bright_ti4" in df.columns else df["brightness"]
    out["brightness_k"] = brillo.astype(float)

    out["frp_mw"] = pd.to_numeric(df.get("frp"), errors="coerce").fillna(0.0)
    # `df.get(col, "")` devolvería el str por defecto, no una Serie, y .astype
    # reventaría. Las columnas opcionales se comprueban contra el índice.
    out["daynight"] = df["daynight"].astype(str) if "daynight" in df.columns else ""
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

    consultar_cuota()

    jobs = [(s, k, a) for s in FIRMS_SOURCES for k, a in AREAS.items()]
    frames: list[pd.DataFrame] = []

    with (
        httpx.Client(follow_redirects=True) as client,
        ThreadPoolExecutor(max_workers=6) as pool,
    ):
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
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        path = RAW / f"firms_{stamp}.parquet"
        df.to_parquet(path, index=False)
        log.info("Raw guardado en %s (%d filas)", path, len(df))

    log.info("FIRMS: %d hotspots crudos", len(df))
    return df
