"""Contexto de cada incendio: viento, avisos, cortes de carretera y ritmo.

Todo lo de aquí **cruza capas que ya se publican**. No añade ninguna fuente ni
ninguna petición: reordena datos que el pipeline ya tiene, para responder la
pregunta que de verdad se hace quien abre el visor —*¿viene hacia mí?*— en vez
de dejarla como deberes del usuario.

Por qué en el pipeline y no en el frontend. Las capas de contexto se cargan en el
navegador solo cuando enciendes su conmutador, así que calcular esto en el cliente
daría una ficha que dice cosas distintas según qué botones hayas pulsado antes.
Además aquí se puede probar.

**Nada de esto es una predicción.** Se publica el viento *observado* en la
posición del incendio, el aviso que *AEMET* declara sobre esa zona y la superficie
nueva *ya detectada* en las últimas horas. Combinarlos para afirmar hacia dónde va
a avanzar el fuego sería una predicción nuestra, y este proyecto no tiene
autoridad para hacerla ante alguien que está mirando si arde algo cerca de su
casa.

Lo que **no** está aquí y se echa en falta: la distancia al núcleo de población
más cercano, que es la pregunta literal del usuario. No se implementa porque la
capa del IGN que tenemos son **polígonos municipales**, no núcleos: usar su
centroide como "el pueblo" daría un error típico de 3,3 km y de hasta 23,6 km en
el municipio más grande. Hace falta la capa de entidades de población, y hasta
tenerla es mejor no decir nada que decir un número así.
"""

from __future__ import annotations

import logging

import geopandas as gpd
import numpy as np
import pandas as pd

from .config import CRS_METRIC_CANARIAS, CRS_METRIC_MAINLAND

log = logging.getLogger(__name__)

# Exponente de la ponderación IDW, el mismo que usa la animación del frontend.
# 2 es el valor habitual: más alto escalona el campo, más bajo lo aplana hasta
# perder los contrastes.
IDW_POTENCIA = 2.0

# Más allá de esto no se interpola. La rejilla tiene un nodo cada 0,75° (~80 km),
# así que un incendio a más de 120 km del nodo más próximo está fuera de la malla
# —en el mar, o en un hueco de cobertura— y darle un viento sería inventarlo.
MAX_DISTANCIA_VIENTO_KM = 120.0

# Radio para considerar que un corte de carretera es "de este incendio".
# 15 km es generoso a propósito: un incendio grande corta accesos lejos de su
# perímetro, y la alternativa —no enseñar un corte que sí es relevante— es peor
# que enseñar uno que quizá no lo sea. El texto nunca afirma la causa: dice
# "cerca", y la causa solo se declara cuando la DGT la declara.
RADIO_CORTES_KM = 15.0

# Ventana para medir el ritmo. 6 h es aproximadamente el hueco entre pasadas de
# VIIRS: más corto y la mayoría de incendios no tendrían dos observaciones que
# comparar; más largo y un incendio que se reactivó hace una hora se diluiría en
# el promedio.
VENTANA_RITMO_H = 6.0

# Superficie que cubre un hotspot, la misma constante que usa `cluster.py` para
# `area_est_ha`. Se comparte a propósito: si el ritmo usara otra, el crecimiento
# publicado no cuadraría con la superficie publicada y no habría forma de saber
# cuál de las dos mentía.
AREA_POR_FOCO_HA = 14.06

CAMPOS_CONTEXTO = [
    "viento_kmh",
    "viento_rachas_kmh",
    "viento_hacia_deg",
    "viento_cardinal_desde",
    "temp_c",
    "humedad_pct",
    "aviso_nivel",
    "aviso_nivel_orden",
    "aviso_fenomeno",
    "aviso_titular",
    "cortes_cerca",
    "cortes_cerca_por_incendio",
    "focos_recientes",
    "crecimiento_ha_h",
]


def _crs_metrico(lon: float) -> int:
    return CRS_METRIC_CANARIAS if lon < -12 else CRS_METRIC_MAINLAND


def _vacio(incidents: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Añade las columnas de contexto vacías.

    Se hace incluso cuando no hay datos de contexto: el frontend lee estos
    nombres, y una columna ausente y una columna nula se comportan distinto en
    GeoJSON. Nula es un "no se sabe" explícito; ausente es un campo que el
    frontend intenta leer y no encuentra.
    """
    salida = incidents.copy()
    for campo in CAMPOS_CONTEXTO:
        if campo not in salida.columns:
            salida[campo] = np.nan
    return salida


def anadir_viento(
    incidents: gpd.GeoDataFrame, viento: gpd.GeoDataFrame | None
) -> gpd.GeoDataFrame:
    """Interpola el viento observado en la posición de cada incendio.

    IDW sobre los nodos publicados, igual que la animación del mapa. Entre dos
    nodos el resultado es siempre una mezcla de los dos y nunca un valor que
    ninguno respalde, que es lo que hace defendible interpolar aquí.

    La dirección que se publica es `viento_hacia_deg`: **hacia dónde sopla**, no
    de dónde viene. Es la que contesta la pregunta útil. El punto cardinal de
    origen se conserva aparte porque es como se nombra el viento en castellano
    —«viento del norte» sopla hacia el sur— y mezclar los dos convenios es el
    error clásico de esta capa.
    """
    salida = _vacio(incidents)
    if viento is None or viento.empty or salida.empty:
        return salida

    nodos = viento.dropna(subset=["direction_to_deg"])
    if nodos.empty:
        return salida

    nx = nodos.geometry.x.to_numpy()
    ny = nodos.geometry.y.to_numpy()

    # El rumbo no se promedia como un número: entre 350° y 10° la media
    # aritmética da 180°, exactamente el sentido contrario. Se promedian las
    # componentes del vector unitario y se recompone el ángulo al final.
    rad = np.deg2rad(nodos["direction_to_deg"].to_numpy(dtype=float))
    ux, uy = np.sin(rad), np.cos(rad)

    velocidad = pd.to_numeric(nodos.get("speed_kmh"), errors="coerce").to_numpy(dtype=float)
    rachas = pd.to_numeric(nodos.get("gusts_kmh"), errors="coerce").to_numpy(dtype=float)
    temp = pd.to_numeric(nodos.get("temp_c"), errors="coerce").to_numpy(dtype=float)
    humedad = pd.to_numeric(nodos.get("humedad_pct"), errors="coerce").to_numpy(dtype=float)

    filas = []
    for punto in salida.geometry:
        if punto is None or punto.is_empty:
            filas.append({})
            continue

        # Grados a km aproximados: la longitud se corrige por el coseno de la
        # latitud. Basta para decidir vecindad y ponderar, no se publica.
        dlat = (punto.y - ny) * 111.32
        dlon = (punto.x - nx) * 111.32 * np.cos(np.deg2rad(punto.y))
        dist = np.hypot(dlat, dlon)

        if dist.min() > MAX_DISTANCIA_VIENTO_KM:
            filas.append({})
            continue

        peso = 1.0 / np.maximum(dist, 0.5) ** IDW_POTENCIA
        total = peso.sum()

        hacia = (np.degrees(np.arctan2(
            float(np.nansum(ux * peso) / total),
            float(np.nansum(uy * peso) / total),
        )) + 360.0) % 360.0

        filas.append({
            "viento_kmh": round(float(np.nansum(velocidad * peso) / total), 1),
            "viento_rachas_kmh": round(float(np.nansum(rachas * peso) / total), 1),
            "viento_hacia_deg": round(hacia, 1),
            # De "sopla hacia" a "viene de": el punto cardinal es el de origen.
            "viento_cardinal_desde": _cardinal((hacia + 180.0) % 360.0),
            "temp_c": round(float(np.nansum(temp * peso) / total), 1),
            "humedad_pct": round(float(np.nansum(humedad * peso) / total), 0),
        })

    interpolados = pd.DataFrame(filas, index=salida.index)
    for columna in interpolados.columns:
        salida[columna] = interpolados[columna]

    con_viento = int(salida["viento_kmh"].notna().sum())
    log.info("Contexto: viento interpolado en %d/%d incendios", con_viento, len(salida))
    return salida


_CARDINALES = (
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSO", "SO", "OSO", "O", "ONO", "NO", "NNO",
)


def _cardinal(deg: float) -> str:
    """Punto cardinal en castellano: O de oeste, no W."""
    return _CARDINALES[int((float(deg) % 360) / 22.5 + 0.5) % 16]


def anadir_avisos(
    incidents: gpd.GeoDataFrame, avisos: gpd.GeoDataFrame | None
) -> gpd.GeoDataFrame:
    """Marca el aviso oficial de AEMET vigente sobre la zona del incendio.

    Si hay varios —calor y viento a la vez, que es lo habitual en julio— se
    publica **el más grave**. Mismo criterio que `_worst_status` en la fusión y
    por la misma razón: quedarse corto es el error caro.

    El aviso se declara sobre una comarca entera, así que decir "hay aviso en
    esta zona" es exacto, mientras que decir "hay aviso en este incendio" no lo
    sería. El texto de la ficha usa la primera forma.
    """
    salida = _vacio(incidents)
    if avisos is None or avisos.empty or salida.empty:
        return salida

    cruce = gpd.sjoin(
        salida[["geometry"]],
        avisos[["nivel", "nivel_orden", "fenomeno", "titular", "geometry"]],
        how="left",
        predicate="within",
    )
    # Un incendio dentro de dos avisos aparece dos veces: gana el más grave.
    cruce = cruce.sort_values("nivel_orden", ascending=False)
    cruce = cruce[~cruce.index.duplicated(keep="first")]

    for origen, destino in (
        ("nivel", "aviso_nivel"),
        ("nivel_orden", "aviso_nivel_orden"),
        ("fenomeno", "aviso_fenomeno"),
        ("titular", "aviso_titular"),
    ):
        salida[destino] = cruce[origen].reindex(salida.index)

    con_aviso = int(salida["aviso_nivel"].notna().sum())
    log.info("Contexto: %d/%d incendios bajo aviso de AEMET", con_aviso, len(salida))
    return salida


def anadir_cortes(
    incidents: gpd.GeoDataFrame, cortes: gpd.GeoDataFrame | None
) -> gpd.GeoDataFrame:
    """Cuenta los cortes de carretera próximos a cada incendio.

    Se publican dos números distintos y no uno: los cortes que hay cerca, y los
    que **la DGT declara causados por incendio forestal**. La distinción es la
    misma que hace `trafico.py` y se conserva por lo mismo: la causa es un dato
    declarado por la DGT, nunca deducido de la proximidad. Un corte por obras a
    3 km de un fuego sigue siendo un corte por obras.
    """
    salida = _vacio(incidents)
    if cortes is None or cortes.empty or salida.empty:
        return salida

    crs = _crs_metrico(float(salida.geometry.x.mean()))
    inc_m = salida[["geometry"]].to_crs(crs)
    cortes_m = cortes[["por_incendio", "geometry"]].to_crs(crs)

    # Buffer en metros y conteo por incendio. Un `sjoin` con el buffer es un
    # solo pase espacial en lugar de una distancia por pareja.
    zona = inc_m.copy()
    zona["geometry"] = zona.geometry.buffer(RADIO_CORTES_KM * 1000.0)

    cruce = gpd.sjoin(zona, cortes_m, how="left", predicate="intersects")
    por_incidente = cruce.groupby(level=0).agg(
        cortes_cerca=("index_right", "count"),
        cortes_cerca_por_incendio=("por_incendio", lambda s: int(s.fillna(False).sum())),
    )

    salida["cortes_cerca"] = por_incidente["cortes_cerca"].reindex(salida.index).fillna(0).astype(int)
    salida["cortes_cerca_por_incendio"] = (
        por_incidente["cortes_cerca_por_incendio"].reindex(salida.index).fillna(0).astype(int)
    )

    afectados = int((salida["cortes_cerca"] > 0).sum())
    log.info("Contexto: %d/%d incendios con cortes a menos de %.0f km",
             afectados, len(salida), RADIO_CORTES_KM)
    return salida


def enriquecer(
    incidents: gpd.GeoDataFrame,
    viento: gpd.GeoDataFrame | None = None,
    avisos: gpd.GeoDataFrame | None = None,
    cortes: gpd.GeoDataFrame | None = None,
    hotspots: gpd.GeoDataFrame | None = None,
    ahora: pd.Timestamp | None = None,
) -> gpd.GeoDataFrame:
    """Aplica los cuatro cruces. Cada uno es independiente de los demás."""
    salida = anadir_viento(incidents, viento)
    salida = anadir_avisos(salida, avisos)
    salida = anadir_cortes(salida, cortes)
    return anadir_ritmo(salida, hotspots, ahora)


def anadir_ritmo(
    incidents: gpd.GeoDataFrame,
    hotspots: gpd.GeoDataFrame | None,
    ahora: pd.Timestamp | None = None,
) -> gpd.GeoDataFrame:
    """Ritmo de crecimiento **observado**, no previsto.

    Es la diferencia entre un incendio que avanza y uno que se está apagando, y
    hoy la aplicación no la dice: dos incendios de 28 ha se ven idénticos aunque
    uno lleve creciendo seis horas y el otro no se haya movido.

    Se mide contando focos nuevos en las últimas `VENTANA_RITMO_H` horas y
    convirtiéndolos a superficie con la **misma** constante que `cluster.py` usa
    para `area_est_ha`. Compartir la constante no es un detalle: con dos
    distintas, el crecimiento publicado no cuadraría con la superficie publicada
    y no habría manera de saber cuál de las dos miente.

    Lo que este número **no** es: una predicción. Dice cuánta superficie nueva se
    ha detectado en las últimas horas. No dice cuánta se detectará en las
    próximas, y la ficha no lo insinúa.

    Cuidado con leerlo como cero: un incendio sin focos recientes puede estar
    apagado, bajo nube, o simplemente no haber tenido pasada. Por eso se publican
    los dos números —focos recientes y ritmo— y no solo el segundo.
    """
    salida = _vacio(incidents)
    if hotspots is None or hotspots.empty or salida.empty:
        return salida
    if "fire_id" not in hotspots.columns or "acq_dt" not in hotspots.columns:
        return salida

    ahora = ahora or pd.Timestamp.now(tz="UTC")
    visto = pd.to_datetime(hotspots["acq_dt"], errors="coerce", utc=True)
    recientes = hotspots[visto >= ahora - pd.Timedelta(hours=VENTANA_RITMO_H)]

    cuenta = recientes.groupby("fire_id").size()
    focos = cuenta.reindex(salida["id"]).fillna(0).astype(int)

    salida["focos_recientes"] = focos.to_numpy()
    salida["crecimiento_ha_h"] = (
        focos.to_numpy() * AREA_POR_FOCO_HA / VENTANA_RITMO_H
    ).round(1)

    creciendo = int((salida["focos_recientes"] > 0).sum())
    log.info(
        "Contexto: %d/%d incendios con focos en las últimas %.0f h",
        creciendo, len(salida), VENTANA_RITMO_H,
    )
    return salida
