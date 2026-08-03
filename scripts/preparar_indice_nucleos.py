"""Índice de búsqueda de núcleos de población para el frontend.

Por qué existe un fichero aparte en vez de leer `config/nucleos.geojson` desde
el navegador: la capa del IGN son 6,2 MB con geometría, y el presupuesto de
carga inicial de todo el visor son 900 KB (RNF-02). Aquí se tira la geometría y
se deja lo justo para buscar y centrar el mapa: nombre, coordenada y población.

El resultado son ~1,3 MB en claro y ~520 KB comprimido, que **sigue siendo
demasiado para la carga inicial**. Por eso el frontend lo pide al primer tecleo
en el buscador y no antes: quien nunca busca no lo paga.

Se ordena por población descendente porque el buscador se queda con los primeros
aciertos, y ante «Villanueva» —hay decenas— el que se busca casi siempre es el
más grande.

    python scripts/preparar_indice_nucleos.py
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
ORIGEN = RAIZ / "config" / "nucleos.geojson"
DESTINO = RAIZ / "web" / "public" / "nucleos-indice.json"

# Por debajo de esto la capa del IGN trae entidades sin nombre útil o duplicadas
# del municipio. No filtramos por población: una aldea de 30 habitantes es
# exactamente lo que alguien busca cuando teme por su casa.
MINIMO_ESPERADO = 30_000

log = logging.getLogger(__name__)


def construir(origen: Path = ORIGEN) -> list[list]:
    """[nombre, lat, lon, habitantes] por núcleo, los más poblados primero."""
    datos = json.loads(origen.read_text(encoding="utf-8"))

    filas: list[list] = []
    for rasgo in datos.get("features", []):
        geometria = rasgo.get("geometry") or {}
        if geometria.get("type") != "Point":
            continue
        nombre = (rasgo.get("properties") or {}).get("nombre")
        if not nombre:
            continue
        lon, lat = geometria["coordinates"][:2]
        habitantes = int(rasgo["properties"].get("habitantes") or 0)
        # Cuatro decimales son ~11 m: de sobra para centrar el mapa, y recortan
        # un tercio del fichero frente a la precisión completa.
        filas.append([nombre, round(lat, 4), round(lon, 4), habitantes])

    filas.sort(key=lambda f: (-f[3], f[0]))
    return filas


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if not ORIGEN.exists():
        raise SystemExit(f"Falta {ORIGEN}. Ejecuta antes scripts/preparar_nucleos.py")

    filas = construir()

    # Puerta de seguridad: si el origen se corrompe o cambia de esquema, esto
    # produciría un índice diminuto y el buscador diría «no existe» para pueblos
    # que sí existen. Un fallo silencioso, que es el que más daño hace aquí.
    if len(filas) < MINIMO_ESPERADO:
        raise SystemExit(
            f"Solo {len(filas)} núcleos, esperados >{MINIMO_ESPERADO}. "
            "No se sobrescribe el índice."
        )

    DESTINO.parent.mkdir(parents=True, exist_ok=True)
    DESTINO.write_text(
        json.dumps(filas, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    log.info("%d núcleos -> %s (%.1f MB)", len(filas), DESTINO, DESTINO.stat().st_size / 1e6)


if __name__ == "__main__":
    main()
