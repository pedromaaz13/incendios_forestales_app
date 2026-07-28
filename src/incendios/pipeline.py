"""Orquestador. Un solo punto de entrada: `python -m incendios.pipeline`.

El orden de este fichero es el contrato de la sección 3, y no es arbitrario:

    ingesta → limpieza → clustering → enriquecimiento → fusión
            → validación → guardas → publicación atómica

Las dos últimas fases son las que impiden publicar una mentira. `validate`
comprueba los ocho invariantes y `publish` compara el recuento con las
ejecuciones anteriores; cualquiera de las dos aborta con código distinto de cero
y entonces **no se escribe nada**. El frontend sigue leyendo el manifiesto
anterior, cuya edad crece a la vista, y eso es honesto. Publicar datos corruptos
con marca de tiempo fresca no lo es.

Un fallo en una fuente de contexto —viento, calidad del aire— no aborta nada: el
mapa sin ellas sigue sirviendo, y tumbar la publicación de incendios porque no
responde un servicio meteorológico gratuito sería desproporcionado.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import UTC, datetime

import geopandas as gpd
import pandas as pd

from . import aire as aire_mod
from . import build as build_mod
from . import clean as clean_mod
from . import cluster as cluster_mod
from . import enrich as enrich_mod
from . import export as export_mod
from . import firms
from . import health as health_mod
from . import merge as merge_mod
from . import publish as publish_mod
from . import trafico as trafico_mod
from . import validate as validate_mod
from . import wind as wind_mod
from .config import OUTPUTS
from .sources.adapters import REGISTRY, collect_all
from .sources.base import OfficialSource

log = logging.getLogger("pipeline")


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)-22s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("fiona").setLevel(logging.WARNING)
    logging.getLogger("pyogrio").setLevel(logging.WARNING)


def run(
    persist_raw: bool = True,
    *,
    con_viento: bool = True,
    con_aire: bool = True,
    con_trafico: bool = True,
    outputs=None,
) -> dict:
    """Ejecuta el pipeline completo y devuelve el manifiesto publicado."""
    outputs = outputs or OUTPUTS
    inicio = datetime.now(UTC)
    t0 = time.perf_counter()

    # --- ingesta ------------------------------------------------------------

    crudos = firms.fetch_hotspots(persist_raw=persist_raw)
    if crudos.empty:
        # Distinto de "no hay incendios": FIRMS no ha contestado con datos. La
        # guarda de vaciado no llega a aplicarse porque no hay nada que medir.
        log.error("FIRMS no devolvió datos. Se aborta sin sobrescribir las salidas.")
        raise SystemExit(1)

    official = _recoger_oficiales()

    # --- limpieza y clustering ---------------------------------------------

    # Las tres fases de `clean` se llaman por separado en lugar de usar
    # `clean.clean()` para poder contar qué descartó cada una. Antes se restaba
    # el total y se atribuía todo a la máscara industrial, lo que publicaba 464
    # "focos industriales suprimidos" cuando en realidad eran duplicados entre
    # pasadas de NOAA-20 y NOAA-21. El riesgo 3 de la sección 11 exige que ese
    # número sea cierto: es la única pista si la máscara oculta un incendio real.
    gdf = clean_mod.to_gdf(crudos)
    tras_confianza = clean_mod.filter_confidence(gdf)
    tras_exclusiones = clean_mod.apply_exclusions(tras_confianza)
    hotspots = clean_mod.deduplicate_spatial(tras_exclusiones)

    # Recorte a España. Va después de limpiar y antes de agrupar: así los
    # recuentos del manifiesto son de España y el clustering no une un foco de
    # Zamora con otro de Braganza en el mismo incendio.
    hotspots, fuera_de_espana = enrich_mod.clip_to_spain(hotspots)

    suprimidos_baja = len(gdf) - len(tras_confianza)
    suprimidos_industrial = len(tras_confianza) - len(tras_exclusiones)
    duplicados = len(tras_exclusiones) - len(hotspots)
    log.info(
        "Descartes: %d baja confianza · %d máscara industrial · %d duplicados "
        "· %d fuera de España",
        suprimidos_baja, suprimidos_industrial, duplicados, fuera_de_espana,
    )

    if hotspots.empty:
        log.error("Todos los hotspots cayeron en los filtros. Se aborta.")
        raise SystemExit(1)

    hotspots = cluster_mod.assign_fire_ids(hotspots)
    fires = enrich_mod.enrich_admin(cluster_mod.build_fires(hotspots))
    perimeters = cluster_mod.build_perimeters(hotspots)
    # RF-P-08: un perímetro derivado del hull nunca se publica sin la marca.
    perimeters["is_estimate"] = True

    # --- fusión oficial ↔ satélite -----------------------------------------

    official, fires = merge_mod.match(official, fires)
    incidents = merge_mod.build_incidents(official, fires)

    # --- validación ---------------------------------------------------------

    validate_mod.validate_or_abort(incidents)

    historial = publish_mod.load_history(outputs.runs_json)
    publish_mod.check_not_suspiciously_empty(len(hotspots), historial)

    # --- estado de fuentes --------------------------------------------------

    informe = _informe_de_salud(hotspots, official, inicio)

    manifest = build_mod.build_manifest(
        hotspots,
        incidents,
        official=official,
        suppressed_industrial=suprimidos_industrial,
        suppressed_lowconf=suprimidos_baja,
        deduplicated=duplicados,
        outside_spain=fuera_de_espana,
        pipeline_started_at=inicio,
        now=datetime.now(UTC),
    )
    degradado, motivo = informe.degraded(
        datetime.now(UTC), manifest["worst_data_age_seconds"]
    )
    if degradado:
        manifest["degraded"] = True
        manifest["degraded_reason"] = motivo

    # --- publicación atómica ------------------------------------------------

    viento = wind_mod.fetch() if con_viento else None
    calidad_aire = aire_mod.fetch() if con_aire else None
    cortes = trafico_mod.fetch() if con_trafico else None

    publish_mod.publish_atomically([
        ("capas de datos", lambda: _escribir_datos(
            hotspots, fires, incidents, perimeters, viento, calidad_aire, cortes, outputs
        )),
        ("sources.json", lambda: informe.write(outputs.sources_json)),
        ("manifest.json", lambda: build_mod.write_manifest(manifest, outputs.manifest)),
    ])

    _anotar_ejecucion(outputs, historial, len(hotspots), len(incidents))

    log.info(
        "Pipeline completado en %.1fs · %d incidentes · %d hotspots%s",
        time.perf_counter() - t0,
        len(incidents),
        len(hotspots),
        " · DEGRADADO" if manifest["degraded"] else "",
    )
    return manifest


# --- fases ------------------------------------------------------------------


def _recoger_oficiales() -> pd.DataFrame:
    """Partes autonómicos. Sin endpoints configurados devuelve vacío, no falla.

    Es el estado actual del repo: las cinco fuentes de RF-P-03 están sin
    descubrir y salen como `disabled`. El pipeline tiene que funcionar igual,
    porque la mitad satelital del producto no depende de ellas.
    """
    try:
        return collect_all(only_configured=True)
    except Exception as exc:
        log.error("Fallo recogiendo fuentes oficiales: %s: %s", type(exc).__name__, exc)
        return OfficialSource.empty()


def _informe_de_salud(
    hotspots: gpd.GeoDataFrame,
    official: pd.DataFrame,
    inicio: datetime,
) -> health_mod.HealthReport:
    informe = health_mod.HealthReport()

    instrumento = hotspots["instrument"].astype(str).str.upper()
    for source_id, nombre, prefijo, precision, ttl, critica in (
        ("firms_viirs", "NASA FIRMS · VIIRS", "VIIRS", 375.0, 600, True),
        ("firms_modis", "NASA FIRMS · MODIS", "MODIS", 1000.0, 600, False),
        ("seviri", "EUMETSAT LSA-SAF · SEVIRI", "SEVIRI", 3000.0, 900, False),
    ):
        n = int(instrumento.str.startswith(prefijo).sum())
        informe.add(health_mod.SourceHealth(
            id=source_id, name=nombre, region="España", kind="satelite",
            critical=critica, ttl_seconds=ttl, precision_m=precision,
            # SEVIRI todavía no se ingiere (RF-P-02): sin registros y sin
            # endpoint, es `disabled`, no una fuente caída.
            configured=prefijo != "SEVIRI" or n > 0,
            last_success_at=inicio if n else None,
            records=n,
            attribution="NASA FIRMS" if prefijo != "SEVIRI" else "EUMETSAT LSA-SAF",
        ))

    resultados = {
        sid: bloque for sid, bloque in official.groupby("source_id")
    } if len(official) else {}
    for estado in health_mod.from_official_sources(REGISTRY, resultados, now=inicio):
        informe.add(estado)

    return informe


def _escribir_datos(
    hotspots, fires, incidents, perimeters, viento, calidad_aire, cortes, outputs
) -> None:
    """Todas las capas menos `sources.json` y `manifest.json`.

    Van juntas en un solo paso porque, entre ellas, el orden da igual: lo que
    hace atómica la publicación es que el manifiesto se escriba el último.
    """
    export_mod._write_geojson(hotspots, outputs.hotspots_geojson, export_mod.HOTSPOT_WEB_FIELDS)
    export_mod._write_geojson(fires, outputs.fires_geojson, export_mod.FIRE_WEB_FIELDS)
    export_mod._write_geojson(
        perimeters, outputs.perimeters_geojson, ["fire_id", "hull_area_ha", "is_estimate"]
    )
    export_mod._write_geojson(
        build_mod.incidents_for_web(incidents),
        outputs.incidents_geojson,
        build_mod.INCIDENT_WEB_FIELDS,
    )

    if viento is not None and len(viento):
        export_mod._write_geojson(viento, outputs.wind_geojson, wind_mod.WIND_SCHEMA)

    if calidad_aire is not None and len(calidad_aire):
        export_mod._write_geojson(calidad_aire, outputs.aire_geojson, aire_mod.AIRE_SCHEMA)

    if cortes is not None and len(cortes):
        export_mod._write_geojson(
            cortes, outputs.trafico_geojson, trafico_mod.TRAFICO_SCHEMA
        )

    export_mod.write_pmtiles(outputs.hotspots_geojson, outputs.hotspots_pmtiles, layer="hotspots")
    export_mod.write_history(hotspots)


def _anotar_ejecucion(outputs, historial: list[dict], hotspots: int, incidents: int) -> None:
    """Registra el recuento **después** de publicar con éxito.

    Anotarlo antes contaminaría la mediana con ejecuciones que abortaron, que es
    justo lo que la guarda de vaciado necesita no tener en cuenta.
    """
    stats = publish_mod.RunStats.now(hotspots=hotspots, incidents=incidents)
    publish_mod.save_history(outputs.runs_json, [*historial, stats.__dict__])


def main() -> None:
    parser = argparse.ArgumentParser(description="Pipeline de incendios activos en España")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument(
        "--no-raw", action="store_true", help="no persistir el CSV crudo de FIRMS"
    )
    parser.add_argument(
        "--sin-viento", action="store_true", help="omitir la capa de viento (Open-Meteo)"
    )
    parser.add_argument(
        "--sin-aire", action="store_true", help="omitir la capa de calidad del aire"
    )
    parser.add_argument(
        "--sin-trafico", action="store_true", help="omitir los cortes de la DGT"
    )
    args = parser.parse_args()

    setup_logging(args.verbose)
    manifest = run(
        persist_raw=not args.no_raw,
        con_viento=not args.sin_viento,
        con_aire=not args.sin_aire,
        con_trafico=not args.sin_trafico,
    )
    print(json.dumps(manifest, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
