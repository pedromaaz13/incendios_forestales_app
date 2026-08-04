"""Infraestructura crítica para el frontend · líneas eléctricas y ferrocarril.

Para qué sirve: ver qué infraestructura pasa cerca de un incendio activo. Un
fuego bajo una línea de 400 kV no es lo mismo que uno en mitad del monte, y hoy
el visor no permite distinguirlo salvo que el usuario suba sus propios puntos.

Fuente: OpenStreetMap vía Overpass, sondeado el 04-08-2026.

**Overpass no se llama desde el navegador.** Es un servicio comunitario con
límites de uso: meterlo en el camino de lectura de un visor público sería
abusar de él, y nos dejaría sin capa el día que nos limiten. Se descarga una vez
aquí y se sirve como fichero estático, igual que los núcleos de población.

Los tamaños están medidos, no estimados:

    | Capa           | Elementos | Crudo   | Simplificado | gzip   |
    |----------------|-----------|---------|--------------|--------|
    | Líneas ≥110 kV |     8.584 | 4,02 MB |      1,50 MB | 260 KB |
    | Ferrocarril    |    26.315 |    ~9 MB|      3,49 MB | 440 KB |

La simplificación a ~55 m es invisible a cualquier escala en la que se mire
esto: una línea de alta tensión es recta entre apoyos. Sin ella, las eléctricas
pesan casi el triple.

Solo se toma alta tensión. Los 15 kV de distribución son decenas de miles de
tramos que llenarían el mapa de ruido sin añadir información: lo que importa
para un incendio es la red de transporte.

    python scripts/preparar_infraestructura.py              # descarga y prepara
    python scripts/preparar_infraestructura.py --desde-cache # reusa lo bajado
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import httpx
from shapely.geometry import LineString

RAIZ = Path(__file__).resolve().parents[1]
DESTINO = RAIZ / "web" / "public"
CACHE = RAIZ / "data" / "raw" / "overpass"

OVERPASS = "https://overpass-api.de/api/interpreter"

# Tolerancia de simplificación en grados. 0,0005° son ~55 m en estas latitudes.
TOLERANCIA = 0.0005

# Cinco decimales son ~1 m: de sobra para una capa de contexto y un tercio menos
# de fichero que la precisión completa.
DECIMALES = 5

TIMEOUT = 900.0

# Consultas. El filtro de tensión se hace en Overpass y no aquí para no
# descargar 40 MB de distribución que luego se tiran.
CONSULTAS = {
    "electricas": """
[out:json][timeout:600];
area["ISO3166-1"="ES"]["admin_level"="2"]->.a;
(way["power"="line"]["voltage"~"^([1-9][0-9]{5,})$"](area.a););
out geom;
""",
    "ferrocarril": """
[out:json][timeout:600];
area["ISO3166-1"="ES"]["admin_level"="2"]->.a;
(way["railway"~"^(rail|narrow_gauge)$"]["service"!~"."](area.a););
out geom;
""",
}

# Puertas de seguridad. Si Overpass devuelve muchos menos elementos de los
# medidos, es un fallo suyo o un cambio de esquema, no que España se haya
# quedado sin red eléctrica. Sobrescribir en ese caso dejaría el mapa medio
# vacío sin que nadie lo note, que es el modo de fallo que este proyecto existe
# para no cometer.
MINIMOS = {"electricas": 6_000, "ferrocarril": 18_000}

log = logging.getLogger(__name__)


def descargar(clave: str) -> dict:
    log.info("Overpass: pidiendo %s…", clave)
    respuesta = httpx.post(
        OVERPASS, data={"data": CONSULTAS[clave]}, timeout=TIMEOUT,
        headers={"User-Agent": "incendios-es/1.0 (preparacion de capas)"},
    )
    respuesta.raise_for_status()
    return respuesta.json()


def a_geojson(crudo: dict, clave: str) -> dict:
    """Overpass a GeoJSON compacto, simplificando la geometría."""
    rasgos = []
    for via in crudo.get("elements", []):
        puntos = via.get("geometry") or []
        if len(puntos) < 2:
            continue

        linea = LineString([(p["lon"], p["lat"]) for p in puntos])
        linea = linea.simplify(TOLERANCIA, preserve_topology=False)
        coords = [[round(x, DECIMALES), round(y, DECIMALES)] for x, y in linea.coords]
        if len(coords) < 2:
            continue

        propiedades: dict[str, int] = {}
        if clave == "electricas":
            # En kilovoltios y como entero: el frontend solo lo usa para elegir
            # grosor, y el texto original trae formatos como "220000;132000".
            bruto = (via.get("tags") or {}).get("voltage", "")
            primero = bruto.split(";")[0].strip()
            propiedades["kv"] = int(primero) // 1000 if primero.isdigit() else 0

        rasgos.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": propiedades,
        })

    return {"type": "FeatureCollection", "features": rasgos}


def preparar(clave: str, desde_cache: bool) -> int:
    CACHE.mkdir(parents=True, exist_ok=True)
    bruto_path = CACHE / f"{clave}.json"

    if desde_cache and bruto_path.exists():
        crudo = json.loads(bruto_path.read_text(encoding="utf-8"))
        log.info("%s: reusando %s", clave, bruto_path)
    else:
        crudo = descargar(clave)
        bruto_path.write_text(json.dumps(crudo), encoding="utf-8")

    coleccion = a_geojson(crudo, clave)
    total = len(coleccion["features"])

    if total < MINIMOS[clave]:
        raise SystemExit(
            f"{clave}: solo {total} elementos, esperados >{MINIMOS[clave]}. "
            "No se sobrescribe."
        )

    DESTINO.mkdir(parents=True, exist_ok=True)
    salida = DESTINO / f"{clave}.geojson"
    salida.write_text(
        json.dumps(coleccion, separators=(",", ":"), ensure_ascii=False),
        encoding="utf-8",
    )
    log.info("%s: %d elementos -> %s (%.2f MB)", clave, total, salida,
             salida.stat().st_size / 1e6)
    return total


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--desde-cache", action="store_true",
        help="Reusa la respuesta ya descargada en data/raw/overpass/",
    )
    args = parser.parse_args()

    for clave in CONSULTAS:
        preparar(clave, args.desde_cache)


if __name__ == "__main__":
    main()
