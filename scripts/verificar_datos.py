"""Informe de coherencia entre lo que trae la fuente y lo que se publica.

Responde a una pregunta concreta: **¿los datos que enseña el mapa son los que
NASA FIRMS ha devuelto de verdad?** El pipeline hace muchas cosas entre la
descarga y el GeoJSON —filtra confianza, suprime focos industriales, deduplica,
agrupa— y cada paso es una oportunidad de perder algo sin enterarse.

Esto no sustituye a los tests: los tests comprueban reglas, esto comprueba la
ejecución de hoy. Sale en el resumen de Actions para que un vistazo baste.

Uso:  PYTHONPATH=src python scripts/verificar_datos.py [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from incendios.config import OUTPUTS  # noqa: E402
from incendios.validate import SPAIN_BBOX, check  # noqa: E402

OK = "✅"
AVISO = "⚠️"
FALLO = "❌"


class Informe:
    def __init__(self) -> None:
        self.lineas: list[tuple[str, str]] = []
        self.fallos = 0

    def add(self, marca: str, texto: str) -> None:
        self.lineas.append((marca, texto))
        if marca == FALLO:
            self.fallos += 1

    def imprimir(self) -> None:
        for marca, texto in self.lineas:
            print(f"{marca} {texto}")

    def markdown(self) -> str:
        filas = "\n".join(f"| {m} | {t} |" for m, t in self.lineas)
        return f"| | Comprobación |\n|---|---|\n{filas}"


def verificar() -> Informe:
    inf = Informe()
    ahora = datetime.now(timezone.utc)

    # --- el manifiesto existe y es coherente consigo mismo ------------------

    if not OUTPUTS.manifest.exists():
        inf.add(FALLO, "No hay manifest.json: el pipeline no llegó a publicar")
        return inf

    manifest = json.loads(OUTPUTS.manifest.read_text(encoding="utf-8"))
    contadores = manifest["counts"]

    inf.add(OK, f"manifest.json publicado a las {manifest['generated_at']}")

    # --- lo que dice el manifiesto vs lo que hay en los ficheros -----------

    incidentes = gpd.read_file(OUTPUTS.incidents_geojson)
    hotspots = gpd.read_file(OUTPUTS.hotspots_geojson)

    if len(incidentes) == contadores["incidents_total"]:
        inf.add(OK, f"{len(incidentes)} incidentes, y el manifiesto dice lo mismo")
    else:
        inf.add(
            FALLO,
            f"Descuadre: el manifiesto dice {contadores['incidents_total']} "
            f"incidentes y incidents.geojson tiene {len(incidentes)}",
        )

    if len(hotspots) == contadores["hotspots_24h"]:
        inf.add(OK, f"{len(hotspots)} focos satelitales, y el manifiesto coincide")
    else:
        inf.add(
            FALLO,
            f"Descuadre: el manifiesto dice {contadores['hotspots_24h']} focos "
            f"y hotspots.geojson tiene {len(hotspots)}",
        )

    # --- los invariantes sobre el fichero ya escrito ------------------------

    violaciones = check(incidentes)
    if violaciones:
        for v in violaciones:
            inf.add(FALLO, f"Invariante violado en el fichero publicado: {v}")
    else:
        inf.add(OK, "Los ocho invariantes se cumplen sobre el fichero publicado")

    # --- las coordenadas caen donde deben -----------------------------------

    oeste, sur, este, norte = SPAIN_BBOX
    fuera = (
        ~(hotspots.geometry.x.between(oeste, este) & hotspots.geometry.y.between(sur, norte))
    ).sum()
    if fuera:
        inf.add(FALLO, f"{fuera} focos fuera del bbox de España")
    else:
        inf.add(OK, "Todas las coordenadas caen dentro de España")

    # --- la antigüedad publicada es la real ---------------------------------

    if len(hotspots) and "acq_dt" in hotspots.columns:
        # `worst_data_age_seconds` es el máximo **por familia de sensor**, no la
        # edad del foco más reciente. VIIRS puede llevar 3 h sin pasada mientras
        # SEVIRI acaba de refrescar: el número que se publica es el peor de los
        # dos, porque enseñar el mejor tranquilizaría sin fundamento.
        marcas = pd.to_datetime(hotspots["acq_dt"], utc=True, errors="coerce")
        instrumento = (
            hotspots["instrument"].astype(str).str.upper()
            if "instrument" in hotspots.columns
            else pd.Series(["VIIRS"] * len(hotspots), index=hotspots.index)
        )
        edades = [
            int((pd.Timestamp(ahora) - marcas[mascara].max()).total_seconds())
            for prefijo in ("VIIRS", "MODIS", "SEVIRI")
            if (mascara := instrumento.str.startswith(prefijo)).any()
        ]
        edad_real = max(edades) if edades else 0
        publicada = manifest.get("worst_data_age_seconds") or 0
        desfase = abs(edad_real - publicada)

        # Un minuto de margen cubre lo que tarda el propio pipeline entre que
        # calcula la edad y termina de escribir.
        if desfase <= 120:
            inf.add(
                OK,
                f"La antigüedad publicada ({publicada // 60} min) coincide con "
                f"la del dato más reciente",
            )
        else:
            inf.add(
                FALLO,
                f"La antigüedad publicada ({publicada // 60} min) no cuadra con "
                f"la real ({edad_real // 60} min). Es el fallo que este proyecto "
                "existe para no cometer",
            )

        horas = edad_real / 3600
        if horas > 4:
            inf.add(AVISO, f"El dato más reciente tiene {horas:.1f} h. VIIRS deja huecos entre pasadas")

    # --- lo suprimido queda registrado --------------------------------------

    suprimidos = (
        contadores["hotspots_suppressed_industrial"] + contadores["hotspots_suppressed_lowconf"]
    )
    total_crudo = contadores["hotspots_24h"] + suprimidos
    if total_crudo:
        pct = 100 * suprimidos / total_crudo
        marca = AVISO if pct > 60 else OK
        inf.add(
            marca,
            f"{suprimidos} de {total_crudo} focos suprimidos ({pct:.0f} %): "
            f"{contadores['hotspots_suppressed_industrial']} industriales, "
            f"{contadores['hotspots_suppressed_lowconf']} de confianza baja",
        )

    # --- reparto por origen --------------------------------------------------

    if len(incidentes):
        reparto = incidentes["origin"].value_counts().to_dict()
        inf.add(OK, f"Incidentes por origen: {reparto}")

        sin_municipio = int(incidentes["municipio"].isna().sum())
        if sin_municipio == len(incidentes):
            inf.add(
                AVISO,
                "Ningún incidente tiene municipio: falta config/municipios.geojson "
                "del IGN (RF-P-07). No es un fallo, es una capa sin descargar",
            )

    # --- estado de fuentes ---------------------------------------------------

    if OUTPUTS.sources_json.exists():
        salud = json.loads(OUTPUTS.sources_json.read_text(encoding="utf-8"))
        cuenta: dict[str, int] = {}
        for f in salud["sources"]:
            cuenta[f["status"]] = cuenta.get(f["status"], 0) + 1
        inf.add(OK, f"Estado de fuentes: {cuenta}")

        rotas = [f["name"] for f in salud["sources"] if f["status"] == "error"]
        if rotas:
            inf.add(AVISO, f"Fuentes sin respuesta: {', '.join(rotas)}")

    if manifest.get("degraded"):
        inf.add(AVISO, f"Publicado en modo degradado: {manifest.get('degraded_reason')}")

    return inf


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--markdown", action="store_true", help="salida para el resumen de Actions")
    args = p.parse_args()

    inf = verificar()

    if args.markdown:
        print(inf.markdown())
    else:
        inf.imprimir()

    # Código de salida distinto de cero solo si hay descuadres reales: un aviso
    # (dato viejo, fuente caída) es información, no motivo para fallar el job.
    sys.exit(1 if inf.fallos else 0)


if __name__ == "__main__":
    main()
