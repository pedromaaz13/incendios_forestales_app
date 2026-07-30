"""Adaptadores concretos.

IMPORTANTE — los endpoints están sin rellenar a propósito. No los invento: una
URL falsa que devuelve 404 en silencio es peor que un hueco explícito. Sigue el
protocolo de descubrimiento del README para obtener cada uno y pégalo aquí.

Muchos portales autonómicos publican sobre **ArcGIS FeatureServer**, así que
`ArcGISSource` cubre varios de golpe: solo cambia la URL y el mapeo de campos.
"""

from __future__ import annotations

import logging

import httpx
import pandas as pd

from .base import (
    STATUS_ACTIVE,
    STATUS_CONTROLLED,
    STATUS_EXTINGUISHED,
    STATUS_STABILIZED,
    OfficialSource,
    SourceMeta,
)

log = logging.getLogger(__name__)


def warn_missing_fields(source_id: str, field_map: dict[str, str], presentes: set[str]) -> list[str]:
    """Avisa de los campos del `field_map` que no vienen en el payload.

    Es el modo de fallo del riesgo 1 de la sección 11: una comunidad renombra
    `ESTADO` y `props.get()` devuelve None en silencio. Como `norm_status(None)`
    da 'desconocido' —un estado válido— nada más aguas abajo se entera, y la
    fuente sigue publicándose como `ok` con todas las filas vacías.
    """
    ausentes = sorted({src for src in field_map.values() if src and src not in presentes})
    if ausentes:
        log.warning(
            "Fuente %s: campos ausentes en el payload %s. ¿Ha cambiado el formato? "
            "Campos recibidos: %s",
            source_id,
            ausentes,
            sorted(presentes)[:25],
        )
    return ausentes


# Vocabulario habitual en portales de emergencias españoles.
COMMON_STATUS_MAP = {
    "extingu": STATUS_EXTINGUISHED,
    "control": STATUS_CONTROLLED,
    "estabiliz": STATUS_STABILIZED,
    "activo": STATUS_ACTIVE,
    "en curso": STATUS_ACTIVE,
    "declarado": STATUS_ACTIVE,
}


class ArcGISSource(OfficialSource):
    """Fuente servida por un ArcGIS FeatureServer / MapServer.

    El patrón de query es siempre el mismo:
        {base}/query?where=1%3D1&outFields=*&f=geojson&outSR=4326

    Truco de descubrimiento: pide `?f=pjson` sobre la capa y te devuelve el
    esquema completo con los nombres reales de los campos. Ahorra adivinar.
    """

    def __init__(
        self,
        meta: SourceMeta,
        field_map: dict[str, str],
        status_map: dict[str, str] | None = None,
        where: str = "1=1",
    ):
        self._meta = meta
        self.field_map = field_map  # {columna_destino: campo_origen}
        self.status_map = status_map or COMMON_STATUS_MAP
        self.where = where

    @property
    def meta(self) -> SourceMeta:
        return self._meta

    def fetch_raw(self, client: httpx.Client) -> dict:
        params = {
            "where": self.where,
            "outFields": "*",
            "f": "geojson",
            "outSR": "4326",
            "returnGeometry": "true",
        }
        r = client.get(
            self._meta.url, params=params, headers=self._meta.headers, timeout=30.0
        )
        r.raise_for_status()
        return r.json()

    def parse(self, raw: dict) -> pd.DataFrame:
        feats = raw.get("features", [])
        if not feats:
            return pd.DataFrame()

        presentes: set[str] = set()
        for f in feats[:50]:
            presentes |= set((f.get("properties") or f.get("attributes") or {}).keys())
        warn_missing_fields(self._meta.source_id, self.field_map, presentes)

        rows = []
        for f in feats:
            props = f.get("properties") or f.get("attributes") or {}
            geom = f.get("geometry") or {}
            coords = geom.get("coordinates")
            if not coords:
                # ArcGIS "esriGeometryPoint" usa x/y en vez de coordinates.
                if "x" not in geom:
                    continue
                coords = [geom["x"], geom["y"]]

            row = {"longitude": float(coords[0]), "latitude": float(coords[1])}
            for dest, src in self.field_map.items():
                row[dest] = props.get(src)

            row["raw_status"] = row.get("status")
            row["status"] = self.norm_status(row.get("status"), self.status_map)
            rows.append(row)

        return pd.DataFrame(rows)


class JsonApiSource(OfficialSource):
    """Fuente con un JSON propio (no ArcGIS).

    `extract` recibe el JSON crudo y devuelve una lista de dicts planos. Se pasa
    como función para que añadir una comunidad sea escribir una lambda, no una
    clase nueva.
    """

    def __init__(self, meta: SourceMeta, extract, status_map: dict[str, str] | None = None):
        self._meta = meta
        self.extract = extract
        self.status_map = status_map or COMMON_STATUS_MAP

    @property
    def meta(self) -> SourceMeta:
        return self._meta

    def fetch_raw(self, client: httpx.Client):
        r = client.get(self._meta.url, headers=self._meta.headers, timeout=30.0)
        r.raise_for_status()
        return r.json()

    def parse(self, raw) -> pd.DataFrame:
        rows = self.extract(raw)
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df["raw_status"] = df.get("status")
        df["status"] = df["raw_status"].map(lambda v: self.norm_status(v, self.status_map))
        return df


# ---------------------------------------------------------------------------
# Registro de fuentes. Rellena `url` y `field_map` con lo que descubras.
# `precision_m` es lo más importante que vas a configurar aquí: mídelo, no lo
# copies. Abre un incendio conocido en el portal oficial y compara su
# coordenada con la posición real.
# ---------------------------------------------------------------------------

JCYL = ArcGISSource(
    meta=SourceMeta(
        source_id="jcyl",
        name="Junta de Castilla y León",
        region="Castilla y León",
        url="",  # TODO: descubrir
        precision_m=500,
        ttl_seconds=300,
        attribution="Junta de Castilla y León",
        notes="Publica nivel IGR y medios actuando. La fuente más completa.",
    ),
    field_map={
        "external_id": "",   # TODO
        "status": "",        # TODO
        "municipio": "",     # TODO
        "provincia": "",     # TODO
        "level": "",         # TODO — nivel IGR
        "resources": "",     # TODO
        "reported_at": "",   # TODO
    },
)

INFOCAM = ArcGISSource(
    meta=SourceMeta(
        source_id="infocam",
        name="INFOCAM / FIDIAS",
        region="Castilla-La Mancha",
        url="",  # TODO
        precision_m=6000,  # centroide de municipio: tolerancia amplia a propósito
        ttl_seconds=600,
        attribution="Junta de Comunidades de Castilla-La Mancha",
        notes="Posición al centroide del municipio. NO fusionar con tolerancia fina.",
    ),
    field_map={"external_id": "", "status": "", "municipio": ""},
)

CV112 = JsonApiSource(
    meta=SourceMeta(
        source_id="112cv",
        name="112 Comunitat Valenciana",
        region="Comunitat Valenciana",
        url="",  # TODO
        precision_m=100,  # coordenadas del incidente
        ttl_seconds=300,
        attribution="Generalitat Valenciana · 112",
    ),
    extract=lambda raw: [],  # TODO
)

BOMBERS = JsonApiSource(
    meta=SourceMeta(
        source_id="bombers",
        name="Bombers de la Generalitat",
        region="Cataluña",
        url="",  # TODO: descubrir
        # Estimación de partida del anexo técnico, **no medida**. Cataluña varía
        # según cómo se cargue el parte: unas veces coordenada del incidente,
        # otras el municipio. Hay que medirlo con 5 incendios reales antes de
        # darlo por bueno, porque este número gobierna toda la fusión.
        precision_m=1500,
        ttl_seconds=300,
        attribution="Generalitat de Catalunya · Bombers",
        notes=(
            "El portal de transparencia catalán publica agregados mensuales sin "
            "coordenadas (comprobado 28/07/2026): no sirve para tiempo real. "
            "La URL hay que sacarla del visor d'incendis forestals."
        ),
    ),
    extract=lambda raw: [],  # TODO
)

INFOCA = ArcGISSource(
    meta=SourceMeta(
        source_id="infoca",
        name="Plan INFOCA",
        region="Andalucía",
        url="",  # TODO: descubrir
        # Misma advertencia que en Bombers: estimación, no medición.
        precision_m=1500,
        ttl_seconds=300,
        attribution="Junta de Andalucía · Plan INFOCA",
        notes="Publica estado y medios. Andalucía concentra buena parte del verano.",
    ),
    field_map={
        "external_id": "",   # TODO
        "status": "",        # TODO
        "municipio": "",     # TODO
        "provincia": "",     # TODO
        "resources": "",     # TODO
        "reported_at": "",   # TODO
    },
)

# Las cinco fuentes de RF-P-03. Las cinco tienen la URL vacía **a propósito**:
# una URL inventada devuelve 404 en silencio y el visor lo enseña como "hoy no
# hay incendios en esta comunidad", que es el fallo más peligroso de este
# sistema. Se rellenan con lo que salga de las DevTools sobre el visor
# autonómico; el procedimiento está en `docs/COMO-CONECTAR-LAS-FUENTES.md`.
REGISTRY: list[OfficialSource] = [JCYL, INFOCAM, CV112, BOMBERS, INFOCA]


def collect_all(only_configured: bool = True) -> pd.DataFrame:
    """Recorre el registro y concatena. Salta las fuentes sin URL configurada."""
    frames = []
    with httpx.Client(follow_redirects=True) as client:
        for src in REGISTRY:
            if only_configured and not src.meta.url:
                continue
            frames.append(src.collect(client))

    if not frames:
        return OfficialSource.empty()
    return pd.concat(frames, ignore_index=True)
