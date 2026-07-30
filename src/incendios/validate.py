"""Validación de invariantes antes de publicar · RF-P-14.

Los ocho invariantes de la sección 4.4 son la última puerta antes de que un dato
llegue a alguien que está mirando si arde algo cerca de su casa. Si alguno falla,
**no se publica nada**: el frontend sigue mostrando la ejecución anterior con su
edad real, que crecerá visiblemente, y eso es honesto. Publicar datos corruptos
con marca de tiempo fresca no lo es.

Por eso `validate_or_abort` sale con código distinto de cero: Actions marca la
ejecución en rojo y el `sync` no llega a correr.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import geopandas as gpd
import pandas as pd

log = logging.getLogger(__name__)

# bbox de España incluidas Canarias. Deliberadamente holgado: cortar un incendio
# real por un bbox estrecho sería peor que dejar pasar uno en el mar.
SPAIN_BBOX = (-19.0, 27.0, 5.0, 44.5)  # oeste, sur, este, norte

VALID_ORIGINS = {"satelite", "oficial", "ambos"}
VALID_STATUS = {"activo", "estabilizado", "controlado", "extinguido"}


@dataclass(frozen=True)
class Violation:
    invariant: int
    name: str
    detail: str
    sample: list[str]

    def __str__(self) -> str:
        muestra = f" · ejemplos: {', '.join(self.sample[:5])}" if self.sample else ""
        return f"[invariante {self.invariant}] {self.name}: {self.detail}{muestra}"


def _ids(gdf: gpd.GeoDataFrame, mask) -> list[str]:
    if "id" not in gdf.columns:
        return []
    return [str(v) for v in gdf.loc[mask, "id"].head(5).tolist()]


def check(incidents: gpd.GeoDataFrame) -> list[Violation]:
    """Devuelve la lista de invariantes violados. Vacía = todo correcto."""
    v: list[Violation] = []

    faltan = [c for c in ("id", "origin", "satellite_confirmed", "official_confirmed")
              if c not in incidents.columns]
    if faltan:
        return [
            Violation(0, "esquema incompleto",
                      f"faltan columnas obligatorias del contrato 4.3: {faltan}", [])
        ]

    if incidents.empty:
        return v

    # 1 · id único
    dup = incidents["id"].duplicated(keep=False)
    if dup.any():
        v.append(Violation(
            1, "id duplicado",
            f"{int(dup.sum())} incidentes comparten id",
            [str(x) for x in incidents.loc[dup, "id"].unique()[:5]],
        ))

    # 2 · todo incidente tiene origen
    sat = incidents["satellite_confirmed"].fillna(False).astype(bool)
    off = incidents["official_confirmed"].fillna(False).astype(bool)
    sin_origen = ~(sat | off)
    if sin_origen.any():
        v.append(Violation(
            2, "incidente sin origen",
            f"{int(sin_origen.sum())} incidentes sin confirmación satelital ni oficial",
            _ids(incidents, sin_origen),
        ))

    # 3 · origin == 'ambos' <=> los dos flags
    incoherente = (incidents["origin"] == "ambos") != (sat & off)
    if incoherente.any():
        v.append(Violation(
            3, "origin incoherente con los flags",
            f"{int(incoherente.sum())} incidentes con origin que no cuadra",
            _ids(incidents, incoherente),
        ))

    origen_invalido = ~incidents["origin"].isin(VALID_ORIGINS)
    if origen_invalido.any():
        v.append(Violation(
            3, "origin fuera del vocabulario",
            f"valores no permitidos: {sorted(set(incidents.loc[origen_invalido, 'origin']))}",
            _ids(incidents, origen_invalido),
        ))

    # 4 · first_detected <= last_detected
    if {"first_detected", "last_detected"} <= set(incidents.columns):
        primero = pd.to_datetime(incidents["first_detected"], utc=True, errors="coerce")
        ultimo = pd.to_datetime(incidents["last_detected"], utc=True, errors="coerce")
        invertido = (primero > ultimo).fillna(False)
        if invertido.any():
            v.append(Violation(
                4, "ventana temporal invertida",
                f"{int(invertido.sum())} incidentes con first_detected posterior a last_detected",
                _ids(incidents, invertido),
            ))

    # 5 · geometría dentro de España
    oeste, sur, este, norte = SPAIN_BBOX
    vacia = incidents.geometry.isna() | incidents.geometry.is_empty
    dentro = (
        incidents.geometry.x.between(oeste, este)
        & incidents.geometry.y.between(sur, norte)
    )
    fuera = vacia | ~dentro
    if fuera.any():
        v.append(Violation(
            5, "geometría fuera de España",
            f"{int(fuera.sum())} incidentes fuera del bbox {SPAIN_BBOX} o sin geometría",
            _ids(incidents, fuera),
        ))

    # 6 · position_precision_m > 0
    if "position_precision_m" in incidents.columns:
        precision = pd.to_numeric(incidents["position_precision_m"], errors="coerce")
        mala = ~(precision > 0)
        if mala.any():
            v.append(Violation(
                6, "precisión no positiva",
                f"{int(mala.sum())} incidentes con position_precision_m nula, cero o negativa. "
                "El anillo de incertidumbre de RF-F-03 no se puede dibujar",
                _ids(incidents, mala),
            ))

    # 7 · n_hotspots == 0 implica origin == 'oficial'
    if "n_hotspots" in incidents.columns:
        sin_hotspots = pd.to_numeric(incidents["n_hotspots"], errors="coerce").fillna(0) == 0
        mal = sin_hotspots & (incidents["origin"] != "oficial")
        if mal.any():
            v.append(Violation(
                7, "incidente satelital sin hotspots",
                f"{int(mal.sum())} incidentes con n_hotspots=0 y origin != 'oficial'",
                _ids(incidents, mal),
            ))

    # 8 · extinguido no se publica
    if "status" in incidents.columns:
        extinguido = incidents["status"] == "extinguido"
        if extinguido.any():
            v.append(Violation(
                8, "incendio extinguido publicado",
                f"{int(extinguido.sum())} incidentes con status 'extinguido' en la salida",
                _ids(incidents, extinguido),
            ))

        # `notna()`: el nulo es válido y significa "nadie lo ha declarado".
        estado_invalido = incidents["status"].notna() & ~incidents["status"].isin(VALID_STATUS)
        if estado_invalido.any():
            v.append(Violation(
                8, "status fuera del vocabulario",
                f"valores no permitidos: {sorted(set(incidents.loc[estado_invalido, 'status'].astype(str)))}",
                _ids(incidents, estado_invalido),
            ))

    # 9 · ningún estado sin quien lo afirme
    #
    # Es el invariante que impide repetir el fallo de más alcance que ha tenido
    # este proyecto: los 79 incendios de producción se publicaban con
    # `status = "activo"` y la interfaz los pintaba en rojo con esa palabra, sin
    # que ningún servicio de extinción lo hubiera declarado. "Activo" es una
    # afirmación sobre el presente hecha a partir de una observación de hace
    # horas, y el aviso de dominio prohíbe los verbos de certeza.
    #
    # Va como invariante y no como test porque el modo de fallo es que alguien
    # rellene el hueco con un valor por defecto —`fillna("activo")`— para que la
    # interfaz "quede mejor". Aquí eso aborta la publicación.
    if "status" in incidents.columns and "official_confirmed" in incidents.columns:
        afirmado = incidents["status"].notna()
        sin_respaldo = afirmado & ~incidents["official_confirmed"].fillna(False).astype(bool)
        if sin_respaldo.any():
            v.append(Violation(
                9, "estado afirmado sin parte oficial",
                f"{int(sin_respaldo.sum())} incidentes declaran un estado "
                f"({sorted(set(incidents.loc[sin_respaldo, 'status'].astype(str)))}) "
                "sin confirmación oficial que lo respalde. Una detección satelital "
                "dice que hubo calor, no que el fuego siga vivo",
                _ids(incidents, sin_respaldo),
            ))

    return v


def validate_or_abort(incidents: gpd.GeoDataFrame) -> None:
    """Aborta la publicación si algún invariante falla.

    `SystemExit(1)` y no una excepción cualquiera: el objetivo es que el paso de
    Actions termine en rojo y el `aws s3 sync` posterior no llegue a ejecutarse.
    """
    violaciones = check(incidents)
    if not violaciones:
        log.info("Invariantes: %d incidentes validados, sin violaciones", len(incidents))
        return

    for viol in violaciones:
        log.error("%s", viol)
    raise SystemExit(
        f"Validación fallida: {len(violaciones)} invariante(s) violado(s). "
        "No se publica nada; el frontend conserva la ejecución anterior."
    )
