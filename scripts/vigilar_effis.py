"""Vigila si el WFS de EFFIS vuelve a estar disponible.

EFFIS es el European Forest Fire Information System, del programa Copernicus.
Su WFS anuncia dos capas que ninguna otra fuente da:

  ercc.ba         → perímetros de área quemada **medidos** desde MODIS y
                    Sentinel-2. Sustituirían las envolventes estimadas que
                    calcula `cluster.build_perimeters`, que son una cota
                    inferior grosera derivada del número de píxeles.
  fwi_nuts5.fwi   → el índice de riesgo meteorológico (Fire Weather Index)
                    **oficial** por municipio. Publicado por quien lo define,
                    lo que evita tener que calcularlo por nuestra cuenta sin
                    poder validarlo contra una referencia.

A 28/07/2026 el servicio responde a `GetCapabilities` pero su backend Oracle
falla al pedir datos:

    msOracleSpatialLayerOpen(): Cannot create OCI Handlers. Connection failure.

Este script distingue las tres situaciones —caído, servicio ausente, y
disponible— y sale con código distinto en cada una, para poder encadenarlo en
un cron o en un `until`.

Uso:
    python scripts/vigilar_effis.py            # una comprobación
    python scripts/vigilar_effis.py --esquema  # además, vuelca los campos
"""

from __future__ import annotations

import argparse
import json
import sys

import httpx

WFS = "https://ies-ows.jrc.ec.europa.eu/effis"

CAPAS = {
    "ercc.ba": "áreas quemadas (perímetros medidos)",
    "fwi_nuts5.fwi": "índice de riesgo meteorológico por municipio",
    "ercc.hs_24hrs_point": "focos de calor de las últimas 24 h",
}

# Contacto en el User-Agent, como con cualquier servicio público.
UA = "incendios-es/1.0 (+https://github.com/pedromaaz13/incendios_forestales_app)"

DISPONIBLE, CAIDO, INALCANZABLE = 0, 1, 2


def comprobar(capa: str, con_esquema: bool = False) -> tuple[str, str]:
    """Devuelve (estado, detalle) para una capa. No lanza."""
    params = {
        "service": "WFS",
        "version": "1.1.0",
        "request": "GetFeature",
        "typeName": capa,
        "maxFeatures": "1",
        "outputFormat": "application/json",
    }
    try:
        with httpx.Client(headers={"User-Agent": UA}, timeout=60.0) as c:
            r = c.get(WFS, params=params)
    except Exception as exc:
        return "inalcanzable", f"{type(exc).__name__}: {exc}"

    texto = r.text

    # EFFIS devuelve 200 con un ExceptionReport cuando su base de datos falla,
    # así que hay que mirar el cuerpo y no el código de estado. Es el mismo
    # patrón que FIRMS con la clave agotada.
    if "ExceptionReport" in texto:
        inicio = texto.find("<ows:ExceptionText>")
        fin = texto.find("</ows:ExceptionText>")
        motivo = texto[inicio + 19 : fin].strip() if inicio > 0 else "sin detalle"
        return "caído", motivo

    try:
        datos = json.loads(texto)
    except json.JSONDecodeError:
        return "caído", f"respuesta no-JSON: {texto[:120]}"

    feats = datos.get("features", [])
    if not feats:
        return "vacío", "responde pero sin registros"

    props = feats[0].get("properties", {})
    detalle = f"{len(feats)} registro(s)"
    if con_esquema:
        detalle += f" · campos: {sorted(props)}"
    return "disponible", detalle


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--esquema", action="store_true", help="volcar los campos de cada capa")
    p.add_argument("--json", action="store_true", help="salida en JSON")
    args = p.parse_args()

    resultados = {}
    for capa, descripcion in CAPAS.items():
        estado, detalle = comprobar(capa, args.esquema)
        resultados[capa] = {"estado": estado, "detalle": detalle, "descripcion": descripcion}

    if args.json:
        print(json.dumps(resultados, ensure_ascii=False, indent=2))
    else:
        for capa, r in resultados.items():
            marca = {"disponible": "✅", "vacío": "⚠️ ", "caído": "❌", "inalcanzable": "❌"}[
                r["estado"]
            ]
            print(f"{marca} {capa:22s} {CAPAS[capa]}")
            print(f"   {r['estado']}: {r['detalle'][:150]}")

    estados = {r["estado"] for r in resultados.values()}
    if "disponible" in estados:
        print("\n🎉 EFFIS ha vuelto. Toca integrar perímetros medidos y FWI oficial.")
        print("   Vuelve a lanzarlo con --esquema para ver los campos reales.")
        sys.exit(DISPONIBLE)
    if estados == {"inalcanzable"}:
        sys.exit(INALCANZABLE)
    sys.exit(CAIDO)


if __name__ == "__main__":
    main()
