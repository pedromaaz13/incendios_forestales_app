"""Descarga los núcleos de población del IGN a `config/nucleos.geojson`.

Para qué sirve: contestar la pregunta con la que la gente abre este visor —*¿arde
algo cerca de mi casa?*— con un número en vez de con un mapa que hay que
interpretar.

**Por qué no valía la capa que ya teníamos.** `config/municipios.geojson` son
polígonos de término municipal. Usar su centroide como "el pueblo" da un error
típico de 3,3 km y de hasta 23,6 km en el municipio más grande de España. Un
«el foco está a 4,2 km de tu pueblo» con 23 km de margen es exactamente la falsa
precisión que este proyecto no publica.

Fuente, verificada con la sonda el 30-07-2026:

    https://api-features.ign.es/collections/nuc/items

Es una OGC API - Features del IGN. Colección `nuc`, «Núcleos de población»:
37.497 registros con `nombre`, `habitantes`, `latitud`, `longitud` y `tipo`.

**`skipGeometry=true` no es una optimización, es lo que lo hace viable.** Cada
núcleo trae su huella como MultiPolygon y pesa ~30 KB; los 37.497 completos son
más de 1 GB. Sin geometría son 1,3 KB cada uno, y las coordenadas del centro
vienen igualmente en `latitud`/`longitud`, que es lo único que hace falta para
medir una distancia.

Uso:

    PYTHONPATH=src python scripts/preparar_nucleos.py
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import httpx

RAIZ = Path(__file__).resolve().parents[1]
DESTINO = RAIZ / "config" / "nucleos.geojson"

BASE = "https://api-features.ign.es/collections/nuc/items"

# La API tope a 1.000 por página. Con 37.497 registros son ~38 peticiones.
PAGINA = 1000

# Cotas de validación. Si el resultado se sale de aquí, algo ha cambiado en el
# origen y es mejor no sobrescribir una capa buena con una mala: el mismo
# criterio que `preparar_municipios.py`, y por la misma razón.
MIN_NUCLEOS = 25_000
MAX_NUCLEOS = 60_000

# Núcleos de control. Si alguno falta, la descarga está incompleta aunque el
# recuento total cuadre — que es como se cuela una capa a la que le falta una
# comunidad entera.
CONTROL = ("Madrid", "Barcelona", "Sevilla", "Bilbao", "Santa Cruz de Tenerife")

log = logging.getLogger("preparar_nucleos")

UA = "incendios-es/1.0 (+https://github.com/pedromaaz13/incendios_forestales_app)"


def descargar() -> list[dict]:
    """Recorre las páginas de la colección y devuelve las propiedades."""
    filas: list[dict] = []
    offset = 0

    with httpx.Client(follow_redirects=True, headers={"User-Agent": UA}, timeout=60.0) as c:
        while True:
            r = c.get(BASE, params={
                "limit": PAGINA,
                "offset": offset,
                # Sin esto cada núcleo trae su MultiPolygon y el total pasa de 1 GB.
                "skipGeometry": "true",
            })
            r.raise_for_status()
            cuerpo = r.json()

            lote = cuerpo.get("features") or []
            if not lote:
                break

            for f in lote:
                p = f.get("properties") or {}
                lat, lon = p.get("latitud"), p.get("longitud")
                # Sin coordenadas el registro no sirve para medir distancias, que
                # es lo único que se le va a pedir.
                if lat is None or lon is None:
                    continue
                filas.append({
                    "nombre": p.get("nombre"),
                    "habitantes": p.get("habitantes"),
                    "tipo": p.get("tipo"),
                    "codine": p.get("codine"),
                    "lat": float(lat),
                    "lon": float(lon),
                })

            total = cuerpo.get("numberMatched")
            offset += len(lote)
            log.info("Descargados %d%s", offset, f"/{total}" if total else "")

            if total is not None and offset >= total:
                break

    return filas


def validar(filas: list[dict]) -> list[str]:
    """Comprueba el resultado antes de escribirlo. Devuelve los fallos."""
    fallos: list[str] = []

    if not MIN_NUCLEOS <= len(filas) <= MAX_NUCLEOS:
        fallos.append(
            f"{len(filas)} núcleos, fuera del rango esperado "
            f"[{MIN_NUCLEOS}, {MAX_NUCLEOS}]. ¿Ha cambiado la colección?"
        )

    nombres = {f["nombre"] for f in filas}
    faltan = [n for n in CONTROL if n not in nombres]
    if faltan:
        fallos.append(f"faltan núcleos de control: {faltan}")

    # Cobertura: si falta Canarias o el noroeste, el bbox lo delata.
    lons = [f["lon"] for f in filas]
    lats = [f["lat"] for f in filas]
    if min(lons) > -17.0:
        fallos.append(f"sin cobertura de Canarias (longitud mínima {min(lons):.2f})")
    if max(lats) < 43.0:
        fallos.append(f"sin cobertura del norte (latitud máxima {max(lats):.2f})")

    return fallos


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    log.info("Descargando núcleos de población del IGN...")
    filas = descargar()

    fallos = validar(filas)
    if fallos:
        for f in fallos:
            log.error("✕ %s", f)
        log.error(
            "No se sobrescribe %s. Una capa incompleta produciría distancias "
            "a un núcleo que no es el más cercano, y eso no se detecta mirando "
            "el mapa.", DESTINO.name,
        )
        return 1

    DESTINO.parent.mkdir(parents=True, exist_ok=True)
    DESTINO.write_text(
        json.dumps({
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "nombre": f["nombre"],
                        "habitantes": f["habitantes"],
                        "tipo": f["tipo"],
                    },
                    "geometry": {"type": "Point", "coordinates": [f["lon"], f["lat"]]},
                }
                for f in filas
            ],
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    kb = DESTINO.stat().st_size / 1024
    log.info("✓ %d núcleos escritos en %s (%.0f KB)", len(filas), DESTINO, kb)
    return 0


if __name__ == "__main__":
    sys.exit(main())
