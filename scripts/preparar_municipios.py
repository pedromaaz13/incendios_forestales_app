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
    python scripts/preparar_municipios.py --fichero lineas_limite_gml.zip
    python scripts/preparar_municipios.py --fichero ~/Downloads/lineas_limite_gml
    python scripts/preparar_municipios.py --candidatas     # prueba las conocidas
"""

from __future__ import annotations

import argparse
import logging
import sys
import tempfile
import unicodedata
import zipfile
from pathlib import Path

import geopandas as gpd
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from incendios.config import CONFIG, CRS_WGS84
from incendios.enrich import NAME_CANDIDATES, PROV_CANDIDATES

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
_WFS_IGN = (
    "https://www.ign.es/wfs-inspire/unidades-administrativas"
    "?service=WFS&version=2.0.0&request=GetFeature"
    "&typeName=au:AdministrativeUnit&outputFormat=application/json"
)

CANDIDATAS = [("IGN · WFS INSPIRE de unidades administrativas", _WFS_IGN)]


class CapaNoValida(RuntimeError):
    """La capa descargada no pasa la puerta de validación."""


def _sin_acentos(texto: str) -> str:
    """Minúsculas y sin diacríticos, para comparar topónimos entre grafías."""
    descompuesto = unicodedata.normalize("NFD", str(texto).lower())
    return "".join(c for c in descompuesto if unicodedata.category(c) != "Mn")


def descargar(url: str, destino: Path) -> Path:
    log.info("Descargando %s", url)
    with (
        httpx.Client(follow_redirects=True, timeout=180.0) as c,
        c.stream("GET", url) as r,
    ):
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
    columna = detectar_columna_nombre(gdf)
    if columna is None:
        raise CapaNoValida(
            f"Sin columna de nombre reconocible. Buscadas: {NAME_CANDIDATES_AMPLIADO}, "
            "y ninguna otra columna contiene algo que parezca topónimos. "
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
        nombre = str(encontrado[columna].iloc[0]) if len(encontrado) else "(ninguno)"
        # El IGN publica el topónimo oficial, que en muchos casos es el de la
        # lengua cooficial: "València", "A Coruña", "Donostia/San Sebastián".
        # Comparar sin acentos evita marcar como error lo que es la grafía
        # correcta.
        if _sin_acentos(esperado) not in _sin_acentos(nombre):
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


def preparar(
    origen: Path | list[Path],
    destino: Path = DESTINO,
    lote_provincias: list[Path] | None = None,
) -> dict:
    """Lee, valida, simplifica y escribe. No escribe nada si la validación falla."""
    lote = origen if isinstance(origen, list) else [Path(origen)]
    log.info("Leyendo %s", ", ".join(f.name for f in lote))
    gdf = leer_lote(lote)

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

    # INSPIRE no trae provincia en la capa municipal, pero sí en la de 3rdOrder.
    if provincia is None and lote_provincias:
        gdf = anadir_provincia(gdf, lote_provincias)
        if "provincia" in gdf.columns and gdf["provincia"].notna().any():
            provincia = "provincia"

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


def _parece_nombres(serie) -> bool:
    """¿Los valores de esta columna parecen topónimos?

    `text` es el nombre que INSPIRE da al campo del topónimo, pero también es
    un nombre genérico que podría contener cualquier cosa. En vez de fiarse del
    nombre de la columna se mira el contenido: cadenas, mayoritariamente
    distintas entre sí y sin números sueltos. Un campo de códigos o una etiqueta
    repetida ("Municipio" en todas las filas) no pasa.
    """
    valores = serie.dropna().astype(str)
    # El umbral es bajo a propósito: la misma función se usa sobre la capa de
    # provincias, que solo tiene 53 recintos. Exigir cientos de filas la
    # descartaba y dejaba todos los incendios sin provincia.
    if len(valores) < 20:
        return False
    distintos = valores.nunique() / len(valores)
    con_letras = valores.str.contains(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]", regex=True).mean()
    return distintos > 0.5 and con_letras > 0.95


def detectar_columna_nombre(gdf: gpd.GeoDataFrame) -> str | None:
    """Busca la columna del topónimo, primero por nombre y luego por contenido."""
    for c in NAME_CANDIDATES_AMPLIADO:
        if c in gdf.columns and _parece_nombres(gdf[c]):
            return c
    # Último recurso: cualquier columna de texto cuyo contenido parezca nombres.
    for c in gdf.columns:
        if c != "geometry" and _parece_nombres(gdf[c]):
            log.warning("Columna de nombre deducida por contenido: '%s'", c)
            return c
    return None


def anadir_provincia(municipios: gpd.GeoDataFrame, lote_provincias: list[Path]) -> gpd.GeoDataFrame:
    """Asigna provincia a cada municipio por punto interior.

    El fichero de municipios de INSPIRE no trae provincia, pero el de 3rdOrder
    sí es la capa de provincias. Se cruza por `representative_point` y no por
    centroide: el centroide de un municipio con forma de herradura puede caer
    fuera de su propio polígono, y entonces se asignaría la provincia vecina.
    """
    try:
        provincias = leer_lote(lote_provincias).to_crs(CRS_WGS84)
    except Exception as exc:
        log.warning("No se pudo leer la capa de provincias: %s", exc)
        return municipios

    columna = detectar_columna_nombre(provincias)
    if columna is None or not (40 <= len(provincias) <= 80):
        log.warning(
            "La capa de provincias no es reconocible (%d recintos, columna %s); "
            "se omite la provincia",
            len(provincias), columna,
        )
        return municipios

    puntos = municipios.copy()
    puntos["geometry"] = municipios.geometry.representative_point()

    # Se renombra antes de cruzar: en INSPIRE las dos capas llaman `text` a su
    # topónimo, y `sjoin` resolvería la colisión con sufijos `_left`/`_right`
    # que dependen de la versión de geopandas.
    derecha = provincias[[columna, "geometry"]].rename(columns={columna: "_provincia"})
    cruce = gpd.sjoin(puntos, derecha, how="left", predicate="within")
    cruce = cruce[~cruce.index.duplicated(keep="first")]

    municipios = municipios.copy()
    municipios["provincia"] = cruce["_provincia"].values
    asignadas = int(municipios["provincia"].notna().sum())
    log.info(
        "Provincia asignada a %d/%d municipios (%d provincias distintas)",
        asignadas, len(municipios), municipios["provincia"].nunique(),
    )
    return municipios


# Extensiones que geopandas sabe abrir directamente.
EXTENSIONES = (".shp", ".gml", ".gpkg", ".geojson", ".json", ".kml", ".sqlite")

# Nombres de columna del topónimo. A los que busca `enrich.py` se añaden los del
# esquema INSPIRE, donde el nombre vive en `text` —el contenido de
# GeographicalName/spelling/SpellingOfName/text— y `LocalisedCharacterString`
# solo lleva la etiqueta del tipo ("Municipio"), que es la misma en todas las
# filas y por eso la comprobación de contenido la descarta.
NAME_CANDIDATES_AMPLIADO = (*NAME_CANDIDATES, "text", "NOMBRE_ACTUAL", "nombre_actual")

# La descarga del IGN (BDLJE, esquema INSPIRE) trae dos familias de capas:
#
#   AdministrativeBoundary → líneas: fronteras, autonómicas, provinciales,
#                            municipales. NO sirven: con líneas no hay
#                            point-in-polygon.
#   AdministrativeUnit     → superficies: país, comunidades, provincias,
#                            municipios. La que vale es **4thOrder**.
#
# Se puntúa por nombre de fichero para no depender de que el usuario sepa cuál
# es cuál, y el tipo de geometría lo confirma después.
PREFERIDAS = ("recinto", "municipio", "muni", "adminunit", "administrativeunit")
DESCARTADAS = ("boundary", "linea", "línea", "lineas", "líneas", "line")

# Orden administrativo. 4thOrder son municipios; el resto son unidades más
# grandes que fallarían el recuento pero conviene no probarlas siquiera.
ORDEN_MUNICIPAL = ("4thorder", "4th_order", "4orden", "municipio", "recinto")
ORDEN_SUPERIOR = ("1storder", "2ndorder", "3rdorder", "1st_order", "2nd_order", "3rd_order")


def _familia(nombre: str) -> tuple[int, int]:
    """Puntúa un nombre de fichero. Menor es mejor."""
    n = nombre.lower()
    es_unidad = any(p in n for p in PREFERIDAS)
    es_linea = any(d in n for d in DESCARTADAS)
    es_municipal = any(o in n for o in ORDEN_MUNICIPAL)
    es_superior = any(o in n for o in ORDEN_SUPERIOR)

    if es_linea:
        return (3, 0)  # líneas: lo último
    if es_unidad and es_municipal:
        return (0, 0)  # AdministrativeUnit 4thOrder: justo lo que se busca
    if es_superior:
        return (2, 0)  # país, comunidades, provincias
    if es_unidad:
        return (1, 0)
    return (1, 1)


def candidatos_en(ruta: Path) -> list[list[Path]]:
    """Agrupa los ficheros de una descarga en lotes que se leen juntos.

    Devuelve **listas** de ficheros, no ficheros sueltos, porque el IGN parte
    las capas grandes: "cada archivo .gml contiene como máximo 10000 entidades".
    Los ~8.130 municipios pueden venir repartidos en varios ficheros y probarlos
    de uno en uno haría fallar el recuento en todos. Se concatenan antes de
    validar y el conjunto se valida como una sola capa.
    """
    if ruta.is_file() and ruta.suffix.lower() == ".zip":
        destino = ruta.parent / f"{ruta.stem}_extraido"
        log.info("Descomprimiendo %s", ruta.name)
        with zipfile.ZipFile(ruta) as z:
            z.extractall(destino)
        ruta = destino

    if ruta.is_file():
        return [[ruta]]

    ficheros = [f for f in sorted(ruta.rglob("*")) if f.suffix.lower() in EXTENSIONES]
    if not ficheros:
        raise CapaNoValida(
            f"No hay ninguna capa reconocible en {ruta}. Extensiones buscadas: "
            f"{EXTENSIONES}"
        )

    # Se agrupan por puntuación: todos los ficheros de la misma familia se leen
    # y concatenan juntos.
    lotes: dict[tuple[int, int], list[Path]] = {}
    for f in ficheros:
        lotes.setdefault(_familia(f.name), []).append(f)

    ordenados = [lotes[k] for k in sorted(lotes)]
    log.info("Encontradas %d capas en %d familias:", len(ficheros), len(ordenados))
    for lote in ordenados[:5]:
        muestra = ", ".join(f.name for f in lote[:3])
        extra = f" (+{len(lote) - 3} más)" if len(lote) > 3 else ""
        log.info("    %d fichero(s): %s%s", len(lote), muestra, extra)
    return ordenados


def leer_lote(lote: list[Path]) -> gpd.GeoDataFrame:
    """Lee y concatena un lote de ficheros que forman una sola capa lógica."""
    if len(lote) == 1:
        return gpd.read_file(lote[0])

    log.info("Concatenando %d ficheros de la misma capa", len(lote))
    partes = []
    for f in lote:
        try:
            trozo = gpd.read_file(f)
        except Exception as exc:
            log.warning("    %s ilegible: %s", f.name, exc)
            continue
        if len(trozo):
            partes.append(trozo)

    if not partes:
        raise CapaNoValida("Ningún fichero del lote se pudo leer")

    # Todos los trozos deben venir en el mismo CRS; si no, se reproyectan al
    # del primero antes de concatenar, o las geometrías quedarían mezcladas.
    referencia = partes[0].crs
    partes = [p if p.crs == referencia else p.to_crs(referencia) for p in partes]

    import pandas as pd

    return gpd.GeoDataFrame(pd.concat(partes, ignore_index=True), crs=referencia)


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
    provincias: list[Path] = []

    if args.fichero:
        ruta = Path(args.fichero).expanduser()
        if not ruta.exists():
            print(f"❌ No existe: {ruta}")
            sys.exit(1)
        lotes = candidatos_en(ruta)
        # La capa de provincias es AdministrativeUnit + 3rdOrder. Se busca
        # aparte para poder cruzarla con los municipios y rellenar `provincia`.
        provincias = [
            f
            for lote in lotes
            for f in lote
            if "administrativeunit" in f.name.lower() and "3rdorder" in f.name.lower()
        ]
        if provincias:
            log.info("Capa de provincias localizada: %s", provincias[0].name)
        origenes = [
            (f"local · {lote[0].name}" + (f" +{len(lote) - 1}" if len(lote) > 1 else ""), lote)
            for lote in lotes
        ]
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
                    origen = [descargar(origen, Path(tmp) / "capa.geojson")]
                resumen = preparar(origen, destino, lote_provincias=provincias or None)
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
