"""Prepara la capa municipal para el geocoding inverso · RF-P-07.

Descarga, valida y simplifica los recintos municipales a
`config/municipios.geojson`, que es lo que `enrich.py` usa para poner nombre a
cada incendio. Sin esta capa el visor publica "Ubicación por determinar" en
todos los incidentes, que es correcto pero inútil.

**La puerta de validación es lo importante de este fichero.** Una descarga que
devuelve un HTML de error, una capa recortada a una comunidad o un servicio que
cambió de esquema producirían un fichero que parece válido y asigna municipios
equivocados. Nombrar mal un incendio es peor que no nombrarlo: alguien busca su
pueblo, no lo ve, y se queda tranquilo. Por eso nada se escribe hasta que la
capa pasa cuatro comprobaciones: número de recintos plausible, columna de nombre
reconocible, cobertura del bbox de España y muestreo contra coordenadas de
municipios conocidos.

No se usa Nominatim: la especificación lo prohíbe en RF-P-07 por rate limit y
por meter una dependencia externa en el camino crítico.

Uso:
    python scripts/preparar_municipios.py --url "https://..."
    python scripts/preparar_municipios.py --fichero descarga.gpkg
    python scripts/preparar_municipios.py --candidatas     # prueba las conocidas
"""

from __future__ import annotations

import argparse
import logging
import sys
import tempfile
import zipfile
from pathlib import Path

import geopandas as gpd
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from incendios.config import CONFIG, CRS_WGS84  # noqa: E402
from incendios.enrich import NAME_CANDIDATES, PROV_CANDIDATES  # noqa: E402

log = logging.getLogger("municipios")

DESTINO = CONFIG / "municipios.geojson"

# España tiene ~8.130 municipios. El margen es amplio a propósito: lo que se
# quiere detectar es una capa recortada (una comunidad suelta, ~200) o un
# fichero que no son municipios en absoluto, no una diferencia de censo.
MIN_MUNICIPIOS = 6_500
MAX_MUNICIPIOS = 9_500

# bbox de España incluidas Canarias.
BBOX_ESPANA = (-19.0, 27.0, 5.0, 44.5)

# Tolerancia de simplificación en grados (~100 m). Los recintos del IGN traen
# un detalle de metros que no aporta nada para decir en qué municipio cae un
# punto, y sin simplificar la capa pesa 30 MB y el spatial join se arrastra.
TOLERANCIA_SIMPLIFICACION = 0.001

# Municipios de control repartidos por el país, con coordenadas de su casco
# urbano. Si la capa dice que estos puntos caen en otro sitio, no es la capa
# que creemos que es.
CONTROL = [
    (40.4168, -3.7038, "madrid"),
    (41.3874, 2.1686, "barcelona"),
    (37.3891, -5.9845, "sevilla"),
    (43.3623, -8.4115, "coruña"),
    (39.4699, -0.3763, "valencia"),
    (28.4636, -16.2518, "cruz"),  # Santa Cruz de Tenerife
]

# Candidatas conocidas. NO están verificadas desde este entorno: la puerta de
# validación existe justamente porque no se puede dar por buena una URL sin
# comprobar lo que devuelve.
CANDIDATAS = [
    (
        "IGN · WFS INSPIRE de unidades administrativas",
        "https://www.ign.es/wfs-inspire/unidades-administrativas"
        "?service=WFS&version=2.0.0&request=GetFeature"
        "&typeName=au:AdministrativeUnit&outputFormat=application/json",
    ),
]


class CapaNoValida(RuntimeError):
    """La capa descargada no pasa la puerta de validación."""


def descargar(url: str, destino: Path) -> Path:
    log.info("Descargando %s", url)
    with httpx.Client(follow_redirects=True, timeout=180.0) as c:
        with c.stream("GET", url) as r:
            r.raise_for_status()
            tipo = r.headers.get("content-type", "")
            if "html" in tipo:
                raise CapaNoValida(
                    f"La URL devolvió HTML ({tipo}), no datos. Suele ser una "
                    "página de error o un formulario de descarga."
                )
            with destino.open("wb") as f:
                for trozo in r.iter_bytes():
                    f.write(trozo)

    mb = destino.stat().st_size / 1024 / 1024
    log.info("Descargado: %.1f MB", mb)
    return destino


def validar(gdf: gpd.GeoDataFrame) -> tuple[str, str | None]:
    """Cuatro comprobaciones antes de aceptar la capa. Lanza si alguna falla."""
    # 1 · número de recintos
    if not MIN_MUNICIPIOS <= len(gdf) <= MAX_MUNICIPIOS:
        raise CapaNoValida(
            f"{len(gdf)} recintos: fuera del rango esperado para España "
            f"({MIN_MUNICIPIOS}-{MAX_MUNICIPIOS}). Puede ser una capa recortada "
            "a una comunidad, o provincias en lugar de municipios."
        )

    # 2 · columna de nombre reconocible
    columna = next((c for c in NAME_CANDIDATES if c in gdf.columns), None)
    if columna is None:
        raise CapaNoValida(
            f"Sin columna de nombre reconocible. Buscadas: {NAME_CANDIDATES}. "
            f"Recibidas: {sorted(gdf.columns)[:25]}"
        )
    provincia = next((c for c in PROV_CANDIDATES if c in gdf.columns), None)

    # 3 · cobertura del bbox
    oeste, sur, este, norte = gdf.total_bounds
    o, s, e, n = BBOX_ESPANA
    if oeste < o - 1 or este > e + 1 or sur < s - 1 or norte > n + 1:
        raise CapaNoValida(
            f"El bbox de la capa {tuple(round(v, 2) for v in gdf.total_bounds)} "
            f"se sale de España {BBOX_ESPANA}. ¿Es de otro país?"
        )
    if este - oeste < 10:
        raise CapaNoValida(
            f"El bbox solo abarca {este - oeste:.1f}° de longitud. España son "
            "~24°: la capa está recortada."
        )

    # 4 · muestreo contra municipios conocidos
    fallos = []
    for lat, lon, esperado in CONTROL:
        punto = gpd.GeoDataFrame(
            geometry=gpd.points_from_xy([lon], [lat]), crs=CRS_WGS84
        )
        encontrado = gpd.sjoin(punto, gdf[[columna, "geometry"]], predicate="within")
        nombre = (
            str(encontrado[columna].iloc[0]).lower() if len(encontrado) else "(ninguno)"
        )
        if esperado not in nombre:
            fallos.append(f"({lat}, {lon}) → '{nombre}', se esperaba '{esperado}'")

    # Se tolera un fallo: los cascos urbanos de algunos municipios están en
    # enclaves y una coordenada de centro de ciudad puede caer en el vecino.
    if len(fallos) > 1:
        raise CapaNoValida(
            "El muestreo contra municipios conocidos falla en "
            f"{len(fallos)}/{len(CONTROL)} casos:\n  " + "\n  ".join(fallos)
        )
    for f in fallos:
        log.warning("Control con discrepancia (tolerado): %s", f)

    log.info(
        "Capa válida: %d municipios · nombre en '%s' · provincia en '%s'",
        len(gdf), columna, provincia or "(no disponible)",
    )
    return columna, provincia


def preparar(origen: Path, destino: Path = DESTINO) -> dict:
    """Lee, valida, simplifica y escribe. No escribe nada si la validación falla."""
    log.info("Leyendo %s", origen)
    gdf = gpd.read_file(origen)

    if gdf.crs is None:
        raise CapaNoValida("La capa no declara CRS; no se puede reproyectar con seguridad")

    # Las "líneas límite" del IGN son justamente eso: líneas. Sirven para pintar
    # fronteras, no para decir en qué municipio cae un punto. Rechazarlas aquí
    # con un mensaje claro ahorra media hora de desconcierto.
    if not es_poligonal(gdf):
        raise CapaNoValida(
            f"La capa es de tipo {sorted(set(gdf.geom_type.dropna().unique()))}, "
            "no de polígonos. Para el geocoding inverso hacen falta los "
            "**recintos** municipales, no las líneas límite. Busca en la "
            "descarga un fichero con 'recinto' o 'municipio' en el nombre."
        )

    gdf = gdf.to_crs(CRS_WGS84)

    columna, provincia = validar(gdf)

    columnas = ["geometry", columna] + ([provincia] if provincia else [])
    slim = gdf[columnas].copy()

    # `preserve_topology` evita que la simplificación abra huecos entre
    # municipios vecinos, que dejarían puntos sin asignar.
    antes = slim.memory_usage(deep=True).sum() / 1024 / 1024
    slim["geometry"] = slim.geometry.simplify(
        TOLERANCIA_SIMPLIFICACION, preserve_topology=True
    )

    # Se normalizan los nombres de columna a los que `enrich.py` busca primero,
    # para que no dependa del origen concreto de la capa.
    renombres = {columna: "NAMEUNIT"}
    if provincia:
        renombres[provincia] = "provincia"
    slim = slim.rename(columns=renombres)

    destino.parent.mkdir(parents=True, exist_ok=True)
    slim.to_file(destino, driver="GeoJSON")

    mb = destino.stat().st_size / 1024 / 1024
    log.info(
        "Escrito %s · %d municipios · %.1f MB (en memoria: %.0f→%.0f MB)",
        destino, len(slim), mb, antes, slim.memory_usage(deep=True).sum() / 1024 / 1024,
    )
    return {"municipios": len(slim), "mb": round(mb, 1), "destino": str(destino)}


# Extensiones que geopandas sabe abrir directamente.
EXTENSIONES = (".shp", ".gml", ".gpkg", ".geojson", ".json", ".kml", ".sqlite")

# La descarga del IGN trae varias capas juntas. Solo una sirve: hacen falta
# **recintos** (polígonos), no **líneas límite** (líneas). Con líneas no se
# puede hacer point-in-polygon, así que se prefieren por nombre y, si el nombre
# no lo aclara, se descarta por el tipo de geometría.
PREFERIDAS = ("recinto", "municipio", "muni", "adminunit", "administrativeunit")
DESCARTADAS = ("linea", "línea", "limite", "límite", "boundary", "line")


def candidatos_en(ruta: Path) -> list[Path]:
    """Ordena los ficheros de una carpeta o zip por probabilidad de servir."""
    if ruta.is_file() and ruta.suffix.lower() == ".zip":
        destino = ruta.parent / f"{ruta.stem}_extraido"
        log.info("Descomprimiendo %s", ruta.name)
        with zipfile.ZipFile(ruta) as z:
            z.extractall(destino)
        ruta = destino

    if ruta.is_file():
        return [ruta]

    ficheros = [
        f for f in sorted(ruta.rglob("*")) if f.suffix.lower() in EXTENSIONES
    ]
    if not ficheros:
        raise CapaNoValida(
            f"No hay ninguna capa reconocible en {ruta}. Extensiones buscadas: "
            f"{EXTENSIONES}"
        )

    def prioridad(f: Path) -> tuple[int, int, str]:
        nombre = f.name.lower()
        preferida = any(p in nombre for p in PREFERIDAS)
        descartada = any(d in nombre for d in DESCARTADAS)
        # Menor es mejor: primero las que parecen recintos, al final las que
        # parecen líneas.
        return (0 if preferida and not descartada else 1 if not descartada else 2,
                len(nombre), nombre)

    ordenados = sorted(ficheros, key=prioridad)
    log.info("Capas encontradas (%d), en orden de prioridad:", len(ordenados))
    for f in ordenados[:12]:
        log.info("    %s", f.relative_to(ruta) if ruta in f.parents else f.name)
    return ordenados


def es_poligonal(gdf: gpd.GeoDataFrame) -> bool:
    """Solo los polígonos sirven: con líneas no hay point-in-polygon."""
    tipos = set(gdf.geom_type.dropna().unique())
    return bool(tipos & {"Polygon", "MultiPolygon"})


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", help="URL de la que descargar la capa")
    p.add_argument(
        "--fichero",
        help="fichero, carpeta o .zip local. Si es carpeta o zip, se buscan "
        "dentro las capas y se prueban por orden de probabilidad",
    )
    p.add_argument("--candidatas", action="store_true", help="probar las URL conocidas")
    p.add_argument("--destino", default=str(DESTINO))
    args = p.parse_args()

    destino = Path(args.destino)

    if args.fichero:
        ruta = Path(args.fichero).expanduser()
        if not ruta.exists():
            print(f"❌ No existe: {ruta}")
            sys.exit(1)
        origenes = [(f"local · {c.name}", c) for c in candidatos_en(ruta)]
    elif args.url:
        origenes = [("url indicada", args.url)]
    elif args.candidatas:
        origenes = CANDIDATAS
    else:
        p.print_help()
        sys.exit(1)

    with tempfile.TemporaryDirectory() as tmp:
        for nombre, origen in origenes:
            try:
                if isinstance(origen, str) and origen.startswith("http"):
                    origen = descargar(origen, Path(tmp) / "capa.geojson")
                resumen = preparar(Path(origen), destino)
                print(f"\n✅ {nombre}: {resumen['municipios']} municipios, {resumen['mb']} MB")
                return
            except (CapaNoValida, httpx.HTTPError, OSError) as exc:
                log.error("%s no sirve: %s", nombre, exc)

    print(
        "\n❌ Ninguna fuente sirvió. El pipeline seguirá funcionando sin nombres "
        "de municipio, que es correcto aunque menos útil.\n"
        "   Descarga la capa a mano del Centro de Descargas del CNIG\n"
        "   (https://centrodedescargas.cnig.es, serie 'Líneas límite municipales')\n"
        "   y pásala con --fichero."
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
