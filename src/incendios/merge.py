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

from .config import (
    ACTIVE_WINDOW_HOURS,
    CRS_METRIC_CANARIAS,
    CRS_METRIC_MAINLAND,
    CRS_WGS84,
)

log = logging.getLogger(__name__)

# Margen sobre la precisión declarada. Un incendio se propaga: el frente puede
# estar a kilómetros del punto que notificó la comunidad hace 6 horas.
MATCH_SLACK_M = 3000.0
MATCH_MAX_M = 15000.0

# Ventana temporal. Un incendio oficial notificado hace 3 días no debe
# emparejarse con una detección satelital de esta mañana en el mismo valle.
MATCH_WINDOW_HOURS = 48.0

# Incertidumbre de la posición según el sensor que detectó el incendio.
#
# FIRMS no da un punto: da el centro del píxel donde saltó la anomalía térmica,
# y el incendio puede estar en cualquier parte de ese píxel. VIIRS son 375 m de
# lado y MODIS 1 km, casi el triple, así que publicar 375 m para un incendio que
# solo vio MODIS dibuja un anillo de incertidumbre menor que el real. Eso es
# fingir precisión sobre el dato que la interfaz usa para decir "puede estar en
# cualquier punto de este círculo".
VIIRS_PIXEL_PRECISION_M = 375.0
MODIS_PIXEL_PRECISION_M = 1000.0


def _precision_por_sensor(sensores: pd.Series | None) -> pd.Series | float:
    """Incertidumbre de posición según el sensor que vio el incendio.

    `sensors` llega como texto separado por comas porque un mismo incendio puede
    haber sido detectado por varios satélites. Basta con que uno sea VIIRS para
    que la posición se conozca con 375 m: se toma el mejor, no el peor ni la
    media, porque la detección más fina es la que acota de verdad dónde está.

    Sin la columna se devuelve el valor de VIIRS, que es el caso mayoritario y
    el comportamiento que había antes de distinguir por sensor.
    """
    if sensores is None:
        return float(VIIRS_PIXEL_PRECISION_M)

    tiene_viirs = sensores.fillna("").str.upper().str.contains("VIIRS")
    return np.where(
        tiene_viirs, VIIRS_PIXEL_PRECISION_M, MODIS_PIXEL_PRECISION_M
    ).astype(float)

# Contrato de la sección 4.3. El frontend lee estos nombres; cambiarlos rompe el
# visor en silencio, así que viven aquí y no repartidos por el código.
INCIDENT_SCHEMA = [
    "id",
    "origin",
    "satellite_confirmed",
    "official_confirmed",
    "confirmed_by",
    "status",
    # Quién afirma ese estado: `oficial` o `satelite`. Con `satelite`, `status`
    # es nulo — una detección de calor no dice si el fuego sigue vivo.
    "status_origen",
    "municipio",
    "provincia",
    "igr_level",
    # Los tres numéricos siguen en el contrato porque una fuente puede darlos
    # desglosados. JCyL no: publica un listado de medios que se resume en texto,
    # y trocearlo en tres números sería inventar una estructura que no existe.
    "resources_air",
    "resources_ground",
    "resources_people",
    "resources_text",
    # Cómo describe la fuente dónde está el fuego, con sus palabras. Ninguna
    # otra capa da esto y no se puede derivar de una coordenada.
    "detalle_oficial",
    "n_hotspots",
    # Qué sensores vieron este incendio. Se publica porque cambia cómo hay que
    # leerlo: un incendio visto solo por MODIS tiene 1 km de resolución frente a
    # los 375 m de VIIRS, y la ficha debe poder decirlo.
    "sensors",
    "frp_total_mw",
    "intensity",
    "area_est_ha",
    "position_precision_m",
    "first_detected",
    "last_detected",
    "started_at",
]


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
    # Nula por defecto, no cero: un incendio sin parte oficial no tiene
    # separación que medir, y un cero se leería como «las dos observaciones
    # coinciden exactamente».
    fires["official_separacion_m"] = None

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

    # `sjoin_nearest` va de oficial -> incendio, así que dos partes pueden elegir
    # el mismo cluster. Si se aceptan los dos, ambos reciben el mismo fire_id y
    # `build_incidents` los colapsa en un solo incidente: un incendio real
    # desaparece del mapa (RF-P-06).
    #
    # El desempate es **por fuente**, no global. Un mismo servicio no notifica
    # dos veces el mismo incendio: si 112 CV publica dos partes a 800 m, son dos
    # incendios y solo el más próximo es el del cluster. Pero dos comunidades
    # distintas sí pueden confirmar el mismo frente —un incendio en un límite
    # provincial lo publican las dos— y ahí `confirmed_by` debe listarlas a
    # ambas. Deduplicar por (cluster, fuente) satisface los dos casos.
    candidatos = joined[ok].sort_values("_dist")
    ganadores = candidatos.index[
        ~candidatos.duplicated(subset=["_cand_fire_id", "source_id"], keep="first")
    ]
    descartados = int(ok.sum()) - len(ganadores)
    if descartados:
        log.info(
            "Fusión: %d partes oficiales cedieron su cluster a uno más próximo "
            "y siguen como huérfanos",
            descartados,
        )
    ok = ok & joined.index.isin(ganadores)

    official.loc[ok.values, "fire_id"] = joined.loc[ok, "_cand_fire_id"].values
    official.loc[ok.values, "match_distance_m"] = joined.loc[ok, "_dist"].round(0).values

    # Propagación inversa: un cluster puede quedar confirmado por varias fuentes.
    #
    # Aquí viaja **todo lo que solo sabe el parte oficial**. Durante meses solo
    # subían tres campos, y `igr_level` y los medios se quedaban por el camino:
    # estaban en el contrato 4.3, el frontend los pintaba, y siempre salían
    # nulos. No se notó porque el generador de demostración los rellenaba a mano
    # después, así que la demo enseñaba «Nivel IGR 2 · 16 aéreos» y producción
    # no enseñaba nada. Un dato que solo existe en la demo es peor que no
    # tenerlo: parece que funciona.
    matched = official[official["fire_id"].notna()]
    if not matched.empty:
        by_fire = matched.groupby("fire_id").agg(
            confirmed_by=("source_id", lambda s: ",".join(sorted(set(s)))),
            official_name=("municipio", "first"),
            official_status=("status", _worst_status),
            # El nivel más alto de los partes que confirman este incendio, por
            # el mismo criterio que `_worst_status`: quedarse corto es el error
            # caro. `max` sobre nulos los ignora.
            official_level=("level", "max"),
            official_provincia=("provincia", "first"),
            # Los medios se concatenan si hay varias fuentes: dos comunidades
            # que confirman el mismo frente despliegan cada una los suyos.
            official_resources=("resources", _juntar_medios),
            # La dirección en texto libre del operador. Solo el 112 valenciano
            # la publica hoy —«AP-7 Km364 >sur»— y es lo que sitúa el fuego
            # respecto a una carretera, que es como la gente localiza las cosas.
            official_detalle=("detalle", _primer_texto),
            # Cuánto se separan el parte oficial y el centroide satelital del
            # mismo incendio. Se calculaba desde siempre y se tiraba en cada
            # ejecución, que es la razón de que `precision_m` siga declarado a
            # ojo en `adapters.py` con un comentario de «provisional hasta
            # medirlo».
            #
            # **No es la precisión de la fuente oficial**, es el error
            # combinado: el centroide de un grupo VIIRS tampoco es el punto de
            # ignición y 375 m de resolución ya ponen su parte. Sirve de cota
            # superior, y por eso se llama separación y no precisión.
            #
            # Se agrega con el mínimo: si dos partes confirman el mismo fuego,
            # el que mejor lo sitúa es el que informa de cuánto puede afinar la
            # fuente.
            official_separacion_m=("match_distance_m", "min"),
        )
        fires = fires.set_index("fire_id")
        for columna in by_fire.columns:
            if columna not in fires.columns:
                fires[columna] = None
        fires.update(by_fire)
        fires = fires.reset_index()

    log.info(
        "Fusión: %d/%d oficiales emparejados · %d clusters confirmados",
        int(ok.sum()), len(official), int(fires["confirmed_by"].astype(bool).sum()),
    )
    _registrar_separacion(official)
    return official, fires


def _registrar_separacion(official: pd.DataFrame) -> None:
    """Deja la separación por fuente en el log, con formato buscable.

    Por qué al log y no solo a `sources.json`: ese fichero se **sobrescribe** en
    cada ejecución y nada guarda el histórico, así que publicar el número no
    basta para poder medir `precision_m` — haría falta una serie y solo hay una
    foto. Los registros de Actions duran 90 días, así que esto sí acumula:

        gh run list --workflow=publicar.yml --limit 100 --json databaseId \\
          -q '.[].databaseId' | xargs -I{} gh run view {} --log \\
          | grep SEPARACION

    Es el apaño barato. El sitio bueno es el histórico en R2, que ya está
    escrito en `ingest.yml` y espera una cuenta de Cloudflare.

    El prefijo va en mayúsculas y sin acentos a propósito: es lo que hace que
    `grep` lo encuentre entre miles de líneas de log.
    """
    if "match_distance_m" not in official.columns or official.empty:
        return

    for source_id, bloque in official.groupby("source_id"):
        distancias = pd.to_numeric(bloque["match_distance_m"], errors="coerce").dropna()
        if not len(distancias):
            log.info("SEPARACION %s emparejados=0 mediana_m=- partes=%d",
                     source_id, len(bloque))
            continue
        log.info(
            "SEPARACION %s emparejados=%d mediana_m=%.0f min_m=%.0f max_m=%.0f partes=%d",
            source_id, len(distancias), distancias.median(),
            distancias.min(), distancias.max(), len(bloque),
        )


_STATUS_RANK = {
    "activo": 4,
    "estabilizado": 3,
    "controlado": 2,
    "extinguido": 1,
    "desconocido": 0,
}


def _primer_texto(values: pd.Series) -> str | None:
    """El primer valor no vacío. Para campos donde concatenar no aporta.

    `pd.isna` antes que `str()`: un NaN de pandas se convierte en la cadena
    `"nan"`, que es no vacía y se colaba tal cual en la ficha. Salió en la demo
    como «Dónde: nan», que es peor que no poner nada.
    """
    for v in values:
        if v is None or (not isinstance(v, str) and pd.isna(v)):
            continue
        texto = str(v).strip()
        if texto:
            return texto
    return None


def _juntar_medios(values: pd.Series) -> str:
    """Une los medios de varias fuentes sin repetir ni dejar huecos.

    Mismo cuidado con NaN que `_primer_texto`: sin él, un incendio sin medios
    declarados publicaba la palabra «nan» donde debería ir el despliegue.
    """
    textos = []
    for v in values:
        if v is None or (not isinstance(v, str) and pd.isna(v)):
            continue
        texto = str(v).strip()
        if texto:
            textos.append(texto)
    return " · ".join(dict.fromkeys(textos))


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
    fires["official_confirmed"] = fires["confirmed_by"].astype(bool)
    fires["origin"] = np.where(fires["official_confirmed"], "ambos", "satelite")
    fires["satellite_confirmed"] = True

    # El estado del cluster que produce `build_fires` es activo/inactivo, y ese
    # vocabulario no es el del contrato 4.3 (activo, estabilizado, controlado,
    # extinguido). La traducción no es cosmética, es una decisión de dominio:
    #
    #  - Con parte oficial manda el estado oficial. Es el único que puede
    #    afirmar "controlado": el satélite solo ve calor, no ve bomberos.
    #  - Sin parte oficial y con detección reciente, "activo".
    #  - Sin parte oficial y sin detección reciente **no se publica como
    #    incidente**. No sabemos si se apagó, si está bajo nube o si el satélite
    #    no ha vuelto a pasar; llamarlo "controlado" sería inventar y llamarlo
    #    "activo" sería alarmar. Sus focos siguen en la capa de hotspots, que es
    #    el dato crudo, sin afirmar nada sobre el estado del fuego.
    #
    # **Sin parte oficial no se publica ningún estado.** Antes se publicaba
    # "activo", y la interfaz lo pintaba en rojo con esa palabra. Pero "activo"
    # internamente solo significaba "detectado dentro de la ventana reciente":
    # con 6 h de antigüedad y ninguna pasada posterior, un incendio así puede
    # estar apagado. El aviso de dominio prohíbe los verbos de certeza, y aquello
    # afectaba al 100 % de lo publicado, porque hoy no hay ni un parte oficial.
    #
    # Ahora `status` queda **nulo** y `status_origen` dice quién lo afirma. Lo que
    # el dato satelital sí soporta —cuánto hace que se vio calor ahí— se publica
    # aparte, en `ultima_observacion_h`, y es lo que la interfaz enseña.
    oficial = fires["official_confirmed"] & fires["official_status"].notna()
    inactivo = fires["status"].eq("inactivo") if "status" in fires.columns else False

    fires["status"] = np.where(
        oficial,
        fires["official_status"],
        np.where(inactivo, "_sin_detecciones_recientes", None),
    )
    fires["status_origen"] = np.where(oficial, "oficial", "satelite")
    # La precisión sale del **mejor** sensor que vio el incendio, no de una
    # constante: VIIRS acota a 375 m y MODIS a 1 km. Es la mitad del producto
    # (RF-F-03): dibujar un punto donde solo hay un área es fingir precisión, y
    # dibujar un área menor que la real es la misma mentira en pequeño.
    fires["position_precision_m"] = _precision_por_sensor(fires.get("sensors"))

    orphans = official[official["fire_id"].isna()].copy()
    if not orphans.empty:
        orphans = gpd.GeoDataFrame(
            orphans,
            geometry=gpd.points_from_xy(orphans["longitude"], orphans["latitude"]),
            crs=CRS_WGS84,
        )
        orphans["origin"] = "oficial"
        orphans["satellite_confirmed"] = False
        orphans["official_confirmed"] = True
        orphans["fire_id"] = "off_" + orphans["source_id"] + "_" + orphans["external_id"].astype(str)
        orphans["confirmed_by"] = orphans["source_id"]
        orphans["official_status"] = orphans["status"]
        # El huérfano hereda la precisión declarada por su fuente: los ±6 km de
        # INFOCAM se dibujan como 6 km, no como un punto.
        orphans["position_precision_m"] = orphans["precision_m"].astype(float)
        orphans["n_hotspots"] = 0
        orphans["frp_total_mw"] = 0.0
        # Sin detección satelital no hay ventana temporal observada: el único
        # instante conocido es cuando lo notificó la comunidad.
        orphans["first_detected"] = orphans["reported_at"]
        orphans["last_detected"] = orphans["reported_at"]
        orphans["started_at"] = orphans["reported_at"]
        # Un huérfano es, por definición, un parte oficial: su estado sí es
        # afirmable. Si la fuente no lo declara queda nulo, no "activo".
        orphans["status"] = orphans["official_status"]
        orphans["status_origen"] = "oficial"
        orphans["official_level"] = orphans["level"]
        orphans["official_resources"] = orphans["resources"]
        orphans["official_provincia"] = orphans["provincia"]
        orphans["official_detalle"] = orphans["detalle"]

    parts = [fires]
    if not orphans.empty:
        parts.append(orphans)

    out = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs=CRS_WGS84)

    for c in INCIDENT_SCHEMA:
        if c not in out.columns:
            out[c] = None

    # `id` es el nombre del contrato 4.3; `fire_id` es el interno del pipeline.
    out["id"] = out["fire_id"]

    # Del nombre interno al del contrato. Sin esto, `igr_level` y los medios
    # salían siempre nulos aunque la fuente los publicara: estaban en el
    # esquema, el frontend los pintaba, y nadie los rellenaba nunca.
    out["igr_level"] = pd.to_numeric(out.get("official_level"), errors="coerce")
    out["resources_text"] = out.get("official_resources")
    out["detalle_oficial"] = out.get("official_detalle")

    # La provincia del parte oficial gana a la del geocoding inverso: la declara
    # quien gestiona el incendio, y además el geocoding no la tiene para los
    # huérfanos, que no han pasado por la capa municipal.
    if "official_provincia" in out.columns:
        out["provincia"] = out["official_provincia"].fillna(out["provincia"])
    out["n_hotspots"] = out["n_hotspots"].fillna(0).astype(int)
    out["satellite_confirmed"] = out["satellite_confirmed"].astype(bool)
    out["official_confirmed"] = out["official_confirmed"].astype(bool)
    out["confirmed_by"] = out["confirmed_by"].fillna("")

    # Invariante 8: un incendio extinguido no se publica. Se filtra aquí y no en
    # export para que el recuento del manifest ya cuadre con lo publicado.
    extinguidos = int((out["status"] == "extinguido").sum())
    if extinguidos:
        out = out[out["status"] != "extinguido"].reset_index(drop=True)
        log.info("Incidentes: %d extinguidos filtrados (invariante 8)", extinguidos)

    sin_recientes = int((out["status"] == "_sin_detecciones_recientes").sum())
    if sin_recientes:
        out = out[out["status"] != "_sin_detecciones_recientes"].reset_index(drop=True)
        log.info(
            "Incidentes: %d clusters satelitales sin detecciones en las últimas "
            "%d h, no publicados como incidentes (sus focos siguen en la capa "
            "de hotspots)",
            sin_recientes, ACTIVE_WINDOW_HOURS,
        )

    log.info(
        "Incidentes: %d satelitales · %d oficiales huérfanos",
        len(fires), len(orphans) if not orphans.empty else 0,
    )
    return out
