"""Construye la máscara de falsos positivos a partir del histórico acumulado.

Idea: un incendio forestal arde unos días en un mismo píxel. Una antorcha de
refinería arde 300 días al año. Agregamos el histórico en celdas de ~500 m y
marcamos las que superan un umbral de días distintos con detección.

Uso:
    python scripts/build_exclusions.py --min-days 45 --out config/exclusions_auto.geojson

Requiere al menos una temporada completa en data/history/ para ser fiable.
Antes de eso, usa la semilla manual de config/exclusions.geojson.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb
import geopandas as gpd
from shapely.geometry import Point

ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "data" / "history"

QUERY = """
WITH grid AS (
    SELECT
        -- redondeo a ~0.005 grados (~450 m en latitudes ibéricas)
        round(latitude  / 0.005) * 0.005 AS lat,
        round(longitude / 0.005) * 0.005 AS lon,
        CAST(acq_dt AS DATE)              AS day,
        frp_mw
    FROM read_parquet($glob, hive_partitioning = true)
)
SELECT
    lat,
    lon,
    count(DISTINCT day) AS active_days,
    count(*)            AS detections,
    median(frp_mw)      AS frp_median
FROM grid
GROUP BY lat, lon
HAVING count(DISTINCT day) >= $min_days
ORDER BY active_days DESC
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-days", type=int, default=45)
    ap.add_argument("--out", type=Path, default=ROOT / "config" / "exclusions_auto.geojson")
    args = ap.parse_args()

    glob = str(HISTORY / "**" / "*.parquet")
    con = duckdb.connect()
    df = con.execute(QUERY, {"glob": glob, "min_days": args.min_days}).df()

    if df.empty:
        raise SystemExit("Sin celdas por encima del umbral. Baja --min-days o acumula más histórico.")

    gdf = gpd.GeoDataFrame(
        df.assign(name=lambda d: "auto_" + d.index.astype(str), kind="auto"),
        geometry=[Point(xy) for xy in zip(df["lon"], df["lat"])],
        crs=4326,
    )
    gdf.to_file(args.out, driver="GeoJSON")
    print(f"{len(gdf)} celdas candidatas -> {args.out}")
    print("Revísalas a mano contra ortofoto antes de fusionarlas en exclusions.geojson.")


if __name__ == "__main__":
    main()
