"""Orquestador. Un solo punto de entrada: `python -m incendios.pipeline`."""

from __future__ import annotations

import argparse
import logging
import sys
import time

from . import clean as clean_mod
from . import cluster as cluster_mod
from . import enrich as enrich_mod
from . import export as export_mod
from . import firms


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)-22s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("fiona").setLevel(logging.WARNING)


def run(persist_raw: bool = True) -> dict:
    log = logging.getLogger("pipeline")
    t0 = time.perf_counter()

    raw = firms.fetch_hotspots(persist_raw=persist_raw)
    if raw.empty:
        log.error("Sin datos. Se aborta sin sobrescribir las salidas anteriores.")
        raise SystemExit(1)

    hotspots = clean_mod.clean(raw)
    hotspots = cluster_mod.assign_fire_ids(hotspots)

    fires = cluster_mod.build_fires(hotspots)
    fires = enrich_mod.enrich_admin(fires)
    perimeters = cluster_mod.build_perimeters(hotspots)

    manifest = export_mod.export_all(hotspots, fires, perimeters)

    log.info("Pipeline completado en %.1fs", time.perf_counter() - t0)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Pipeline de incendios activos en España")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument(
        "--no-raw", action="store_true", help="no persistir el CSV crudo de FIRMS"
    )
    args = parser.parse_args()

    setup_logging(args.verbose)
    run(persist_raw=not args.no_raw)


if __name__ == "__main__":
    main()
