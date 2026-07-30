"""Sonda de descubrimiento de endpoints · apoyo a RF-P-03.

**Esto se ejecuta en tu máquina, no en CI.** El agente que escribió el pipeline
no tiene salida a internet, así que no puede abrir los portales autonómicos ni
comprobar si una URL responde. Este script hace ese trabajo y te deja el
resultado en un formato que se puede pegar directamente en una conversación.

Qué hace con una URL:

  1. La pide de verdad y dice qué contestó (código, tipo de contenido, tamaño).
  2. Reconoce el tipo de servicio: ArcGIS FeatureServer/MapServer, WFS de
     GeoServer, o JSON propio.
  3. Enumera los **nombres reales de los campos** y enseña un registro de
     ejemplo, que es lo que hay que meter en el `field_map` del adaptador.
  4. Guarda la respuesta cruda en `tests/fixtures/{source_id}.json`, que es el
     fixture de regresión que exige la sección 8.2.
  5. Propone un `field_map` inicial adivinando por el nombre del campo.

Lo que NO hace: inventar URLs. Si no le das ninguna, prueba una lista de
candidatas conocidas y te dice cuáles responden, pero una candidata que
responde no es necesariamente la buena — hay que mirar lo que devuelve.

Uso:

    # Probar una URL concreta (lo habitual, sacada de DevTools)
    python scripts/descubrir_fuentes.py --url "https://..." --id jcyl

    # Probar todas las candidatas conocidas
    python scripts/descubrir_fuentes.py --explorar

    # Enumerar las capas de un servidor ArcGIS o GeoServer
    python scripts/descubrir_fuentes.py --listar "https://services.arcgis.com/xxx/ArcGIS/rest/services"

    # Comprobar que tus claves funcionan
    python scripts/descubrir_fuentes.py --firms
    python scripts/descubrir_fuentes.py --aemet
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import httpx

RAIZ = Path(__file__).resolve().parents[1]
FIXTURES = RAIZ / "tests" / "fixtures"

# User-Agent identificable con contacto, como exige RF-P-03. Cámbialo por tu
# correo antes de usarlo en serio: si un administrador ve tráfico raro, que
# pueda escribirte en vez de bloquearte.
CONTACTO = os.environ.get("INCENDIOS_CONTACTO", "cambia-esto@ejemplo.org")
UA = f"incendios-es/1.0 (+{CONTACTO})"

TIMEOUT = 25.0

# Candidatas a explorar. Son puntos de partida documentados públicamente
# (portales de datos abiertos, catálogos de IDE), NO endpoints confirmados de
# incendios activos. El script dice cuáles responden; decidir cuál sirve es
# trabajo humano, mirando lo que devuelve.
CANDIDATAS: dict[str, list[str]] = {
    # --- Detección: llenar el 58 % de tiempo ciego ---------------------------
    #
    # Medido el 30-07-2026: de las últimas 24 h, 14 sin ninguna detección, con
    # una racha ciega de 6 h seguidas. VIIRS y MODIS van en órbita polar y solo
    # ven España al pasar por encima.
    #
    # Sentinel-3 SLSTR es el mejor candidato: dos satélites (3A y 3B), ~2 pasadas
    # diarias cada uno, y producto de fuego a **1 km** — la misma sensibilidad
    # que MODIS, no los 3 km de SEVIRI. Con una mediana de 28 ha por incendio,
    # SEVIRI apenas vería ninguno de los que tenemos; Sentinel-3 sí.
    #
    # Estas URL son las **raíces documentadas de los catálogos**, no endpoints de
    # producto: sirven para descubrir qué hay, no para ingerir. Que respondan no
    # significa que sirvan, y hay que mirar qué devuelven.
    "sentinel3": [
        "https://catalogue.dataspace.copernicus.eu/resto/api/collections/Sentinel3/search.json?maxRecords=1",
        "https://catalogue.dataspace.copernicus.eu/stac",
    ],
    # --- Distancia al núcleo de población más cercano ------------------------
    #
    # Es la pregunta literal del usuario —¿arde algo cerca de mi casa?— y hoy no
    # se puede contestar: la capa que tenemos son **polígonos municipales**, y
    # usar su centroide como "el pueblo" daría un error típico de 3,3 km y de
    # hasta 23,6 km en el municipio más grande.
    #
    # Hace falta una capa de **entidades o núcleos de población** (puntos). El
    # CNIG la publica dentro de la BTN, y el IGN tiene servicios INSPIRE de
    # nombres geográficos.
    "nucleos_poblacion": [
        "https://www.ign.es/wfs/nomenclator-geografico?service=WFS&request=GetCapabilities",
        "https://api-features.ign.es/collections",
    ],
    "jcyl": [
        "https://idecyl.jcyl.es/geoserver/incendios/wfs?service=WFS&request=GetCapabilities",
        "https://idecyl.jcyl.es/geoserver/wfs?service=WFS&request=GetCapabilities",
    ],
    "generico_arcgis": [
        "https://services8.arcgis.com/wRYz6wFhbZUtSLje/ArcGIS/rest/services?f=pjson",
    ],
}

# Pistas para adivinar el field_map. Se busca por subcadena en minúsculas.
PISTAS = {
    "external_id": ("objectid", "id_incendio", "codigo", "id", "fid", "num"),
    "status": ("estado", "situacion", "fase", "estat"),
    "municipio": ("municipio", "muni", "termino", "localidad", "poblacion"),
    "provincia": ("provincia", "prov"),
    "level": ("igr", "nivel", "gravedad"),
    "resources": ("medios", "recursos", "dotacio"),
    "reported_at": ("fecha", "alta", "inicio", "deteccion", "data", "hora"),
}


def cliente() -> httpx.Client:
    return httpx.Client(follow_redirects=True, headers={"User-Agent": UA}, timeout=TIMEOUT)


def _titulo(texto: str) -> None:
    print(f"\n{'=' * 72}\n{texto}\n{'=' * 72}")


def sondear(url: str, source_id: str | None = None, guardar: bool = True) -> dict[str, Any]:
    """Pide la URL y describe lo que ha contestado."""
    _titulo(f"Sondeando {url}")

    try:
        with cliente() as c:
            r = c.get(url)
    except Exception as exc:
        print(f"  ✕ No responde: {type(exc).__name__}: {exc}")
        return {"ok": False, "error": str(exc)}

    tipo = r.headers.get("content-type", "?").split(";")[0]
    print(f"  HTTP {r.status_code} · {tipo} · {len(r.content) / 1024:.1f} KB")

    if r.status_code != 200:
        print(f"  ✕ El servidor devolvió {r.status_code}. No sirve.")
        print(f"    Primeros 300 caracteres:\n    {r.text[:300]}")
        return {"ok": False, "status": r.status_code}

    # Un HTML donde se esperaba JSON suele ser una página de error o un
    # cortafuegos. Es el fallo silencioso que este proyecto vigila.
    if "html" in tipo:
        print("  ✕ Ha devuelto HTML, no datos. Suele ser una página de error,")
        print("    un aviso de cookies o un WAF. Esta URL no es el endpoint.")
        return {"ok": False, "html": True}

    try:
        datos = r.json()
    except Exception:
        print("  ⚠ No es JSON. Si es XML puede ser un GetCapabilities de WFS:")
        print("    busca <FeatureType><Name> para ver las capas disponibles.")
        print(f"    Primeros 600 caracteres:\n{r.text[:600]}")
        return {"ok": False, "json": False, "texto": r.text[:2000]}

    resumen = describir(datos)

    if guardar and source_id and resumen.get("registros"):
        FIXTURES.mkdir(parents=True, exist_ok=True)
        destino = FIXTURES / f"{source_id}.json"
        destino.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n  ✓ Fixture guardado en {destino.relative_to(RAIZ)}")
        print("    Este fichero es lo que hace mantenible el scraper: cuando la")
        print("    comunidad cambie el formato en agosto, el test rojo te dirá cuál.")

    return {"ok": True, **resumen}


def describir(datos: Any) -> dict[str, Any]:
    """Reconoce la forma del payload y enumera los campos reales."""
    registros: list[dict] = []
    forma = "desconocida"

    if isinstance(datos, dict) and "features" in datos:
        feats = datos["features"] or []
        forma = "GeoJSON / ArcGIS FeatureServer"
        registros = [
            (f.get("properties") or f.get("attributes") or {}) for f in feats if isinstance(f, dict)
        ]
        geoms = [f.get("geometry") for f in feats[:3] if isinstance(f, dict)]
        print(f"  ✓ {forma} · {len(feats)} features")
        if geoms and geoms[0]:
            print(f"    Geometría de ejemplo: {json.dumps(geoms[0], ensure_ascii=False)[:160]}")

    elif isinstance(datos, dict) and "layers" in datos:
        forma = "índice de servicio ArcGIS"
        print(f"  ✓ {forma}. Capas publicadas:")
        for capa in datos.get("layers", []):
            print(f"      id={capa.get('id')}  {capa.get('name')}")
        print("\n    Pide una capa concreta añadiendo /{id}/query?where=1%3D1&outFields=*&f=geojson")
        return {"forma": forma, "registros": 0}

    elif isinstance(datos, list):
        forma = "lista JSON"
        registros = [d for d in datos if isinstance(d, dict)]
        print(f"  ✓ {forma} · {len(registros)} elementos")

    elif isinstance(datos, dict):
        # JSON propio: se busca la primera lista de objetos que contenga.
        for clave, valor in datos.items():
            if isinstance(valor, list) and valor and isinstance(valor[0], dict):
                forma = f"JSON propio · lista en '{clave}'"
                registros = valor
                print(f"  ✓ {forma} · {len(valor)} elementos")
                break
        else:
            print(f"  ⚠ JSON sin lista de registros. Claves: {list(datos)[:15]}")
            return {"forma": forma, "registros": 0}

    if not registros:
        print("  ⚠ Cero registros. Puede ser correcto (no hay incendios ahora) o")
        print("    puede que el filtro `where` esté excluyéndolo todo. Vuelve a")
        print("    probar en un día con incendios activos antes de darlo por bueno.")
        return {"forma": forma, "registros": 0}

    # Un catálogo OGC API o similar: lo útil es la lista entera de qué publica,
    # no el primer elemento con todos sus campos. Con 11 colecciones y un solo
    # registro impreso no había forma de saber si alguna servía.
    if len(registros) > 1 and all("id" in r for r in registros[:5]):
        print(f"\n  INVENTARIO ({len(registros)} elementos):")
        for r in registros[:40]:
            titulo = r.get("title") or r.get("name") or r.get("description") or ""
            print(f"      {str(r['id'])[:40]:<40} {str(titulo)[:70]}")
        if len(registros) > 40:
            print(f"      ... y {len(registros) - 40} más")

    campos = sorted({k for r in registros[:50] for k in r})
    print(f"\n  CAMPOS REALES ({len(campos)}):")
    for c in campos:
        muestra = next((r[c] for r in registros if r.get(c) not in (None, "")), None)
        print(f"      {c:28s} = {json.dumps(muestra, ensure_ascii=False, default=str)[:70]}")

    print("\n  REGISTRO DE EJEMPLO COMPLETO:")
    print("   ", json.dumps(registros[0], ensure_ascii=False, default=str)[:900])

    proponer_field_map(campos)
    return {"forma": forma, "registros": len(registros), "campos": campos}


def proponer_field_map(campos: list[str]) -> None:
    """Adivina el field_map por el nombre del campo. Es un borrador, no un oráculo."""
    print("\n  FIELD_MAP PROPUESTO (revísalo, está adivinado por el nombre):")
    print("    field_map={")
    for destino, pistas in PISTAS.items():
        elegido = next(
            (c for pista in pistas for c in campos if pista in c.lower()),
            "",
        )
        marca = "" if elegido else "   # <-- NO ENCONTRADO, ponlo a mano"
        print(f'        "{destino}": "{elegido}",{marca}')
    print("    },")
    print("\n    Ojo con `reported_at`: comprueba en qué formato viene la fecha.")
    print("    Muchos ArcGIS publican epoch en milisegundos, no ISO-8601.")


def listar_servicios(url: str) -> None:
    """Enumera las capas de un servidor ArcGIS o de un GeoServer."""
    _titulo(f"Listando servicios de {url}")
    separador = "&" if "?" in url else "?"

    try:
        with cliente() as c:
            r = c.get(f"{url}{separador}f=pjson")
            r.raise_for_status()
            datos = r.json()
    except Exception as exc:
        print(f"  ✕ No se pudo listar: {exc}")
        print("    Si es un GeoServer prueba: ?service=WFS&request=GetCapabilities")
        return

    for servicio in datos.get("services", []):
        print(f"    {servicio.get('type'):16s} {servicio.get('name')}")
    for capa in datos.get("layers", []):
        print(f"    capa id={capa.get('id'):<4} {capa.get('name')}")
    if not datos.get("services") and not datos.get("layers"):
        print(f"    Respuesta sin servicios ni capas. Claves: {list(datos)[:15]}")


def comprobar_firms() -> None:
    """Verifica que FIRMS_MAP_KEY funciona antes de meterla en los secrets."""
    _titulo("Comprobando FIRMS_MAP_KEY")
    clave = os.environ.get("FIRMS_MAP_KEY", "")
    if not clave:
        print("  ✕ FIRMS_MAP_KEY no está en el entorno.")
        print("    Pídela gratis en https://firms.modaps.eosdis.nasa.gov/api/map_key/")
        print("    Luego:  export FIRMS_MAP_KEY=...")
        return

    url = (
        "https://firms.modaps.eosdis.nasa.gov/api/area/csv/"
        f"{clave}/VIIRS_NOAA20_NRT/-9.6,35.85,4.4,43.9/1"
    )
    try:
        with cliente() as c:
            r = c.get(url)
    except Exception as exc:
        print(f"  ✕ Sin conexión con FIRMS: {exc}")
        return

    # Las cabeceras de respuesta, porque ahí es donde FIRMS declara la cuota y
    # asumir el nombre de una cabecera es cómo se publica un campo siempre nulo.
    print("  Cabeceras de la respuesta:")
    for k, v in sorted(r.headers.items()):
        if k.lower().startswith(("x-", "remaining", "ratelimit", "rate-", "quota")):
            print(f"      {k}: {v}")

    texto = r.text.strip()
    primera = texto.splitlines()[0] if texto else ""

    # FIRMS contesta 200 con texto plano cuando la clave falla: hay que mirar el
    # contenido, no el código de estado. Es el fallo que RF-P-01 vigila.
    if "," not in primera:
        print(f"  ✕ La clave no funciona. FIRMS respondió: {texto[:200]}")
        return

    filas = max(0, len(texto.splitlines()) - 1)
    print(f"  ✓ Clave válida · {filas} hotspots en las últimas 24 h en España")
    print(f"    Cabecera CSV: {primera}")
    print("\n    Mete la clave en Settings → Secrets and variables → Actions")
    print("    del repositorio, con el nombre FIRMS_MAP_KEY.")


def _describir_carga(bruto: bytes, tipo: str) -> None:
    """Describe qué hay dentro de una respuesta de AEMET.

    Cada endpoint devuelve una cosa distinta —JSON, XML CAP, un TAR.GZ de XML,
    o un PNG— y el adaptador que haya que escribir depende de cuál sea. Esto
    imprime lo justo para decidirlo sin volcar megabytes en el resumen del job.
    """
    import gzip
    import io
    import tarfile

    # TAR, comprimido o no. Los avisos CAP vienen empaquetados, uno por zona y
    # nivel. AEMET los sirve como `application/x-gtar` sin gzip, así que mirar
    # solo la firma de gzip los dejaba caer en la rama de "texto plano" y salía
    # el volcado binario del tar en vez del esquema.
    es_tar = bruto[257:262] == b"ustar"
    if bruto[:2] == b"\x1f\x8b" or es_tar:
        try:
            crudo = bruto if es_tar else gzip.decompress(bruto)
            with tarfile.open(fileobj=io.BytesIO(crudo)) as t:
                nombres = t.getnames()
                print(f"      TAR con {len(nombres)} ficheros")
                for n in nombres[:5]:
                    print(f"        · {n}")
                primero = next((m for m in t.getmembers() if m.isfile()), None)
                if primero:
                    dentro = t.extractfile(primero)
                    if dentro:
                        _describir_carga(dentro.read(), "application/xml")
        except Exception as exc:
            print(f"      TAR ilegible: {exc}")
        return

    if bruto[:8] == b"\x89PNG\r\n\x1a\n":
        print("      PNG · es un mapa rasterizado, no datos vectoriales")
        return

    texto = bruto.decode("utf-8", errors="replace").strip()

    if texto[:1] in "{[":
        try:
            objeto = json.loads(texto)
        except Exception as exc:
            print(f"      JSON ilegible: {exc}")
            return
        muestra = objeto[0] if isinstance(objeto, list) and objeto else objeto
        if isinstance(muestra, dict):
            print(f"      claves: {', '.join(sorted(muestra)[:25])}")
            print(f"      primer registro:\n{json.dumps(muestra, ensure_ascii=False, indent=2)[:2500]}")
        else:
            print(f"      {texto[:1500]}")
        return

    if texto[:1] == "<":
        # Etiquetas por frecuencia: da la forma del documento sin volcarlo.
        etiquetas = re.findall(r"<([A-Za-z_][\w:.-]*)", texto)
        cuenta = Counter(etiquetas)
        print(f"      XML · {len(cuenta)} etiquetas distintas")
        for nombre, n in cuenta.most_common(30):
            print(f"        {n:>5}  {nombre}")
        print(f"      documento completo:\n{texto[:9000]}")
        return

    print(f"      texto plano:\n{texto[:1500]}")


def comprobar_aemet() -> None:
    """Verifica AEMET_API_KEY y enseña qué desbloquea.

    AEMET publica dos cosas que ninguna otra fuente accesible da hoy: el índice
    oficial de riesgo de incendio para España y los avisos meteorológicos
    oficiales en formato CAP. Es lo que EFFIS iba a aportar, servido por la
    agencia estatal en vez de por un WFS europeo con la base de datos caída.
    """
    _titulo("Comprobando AEMET_API_KEY")
    clave = os.environ.get("AEMET_API_KEY", "")
    if not clave:
        print("  ✕ AEMET_API_KEY no está en el entorno.")
        print("    Pídela gratis en https://opendata.aemet.es/centrodedescargas/altaUsuario")
        print("    Llega por correo en unos minutos. Luego:")
        print("      export AEMET_API_KEY=...")
        return

    endpoints = {
        "incendios/mapasriesgo/estimado/area/p": "índice de riesgo de incendio",
        "avisos_cap/ultimoelaborado/area/esp": "avisos meteorológicos oficiales (CAP)",
    }

    for ruta, descripcion in endpoints.items():
        url = f"https://opendata.aemet.es/opendata/api/{ruta}"
        try:
            with cliente() as c:
                r = c.get(url, params={"api_key": clave})
        except Exception as exc:
            print(f"  ✕ {descripcion}: sin conexión ({exc})")
            continue

        # AEMET contesta 200 con el cuerpo vacío cuando falta o falla la clave,
        # así que hay que mirar el contenido. Mismo patrón que FIRMS.
        cuerpo = r.text.strip()
        if not cuerpo:
            print(f"  ✕ {descripcion}: respuesta vacía. La clave no es válida.")
            continue

        try:
            datos = r.json()
        except Exception:
            print(f"  ⚠ {descripcion}: respuesta no-JSON: {cuerpo[:120]}")
            continue

        # La API devuelve un sobre con la URL real de los datos.
        estado = datos.get("estado")
        if estado != 200:
            print(f"  ✕ {descripcion}: estado {estado} · {datos.get('descripcion')}")
            continue

        print(f"  ✓ {descripcion}")

        # Seguir hasta los datos de verdad. El sobre solo dice "hay algo ahí";
        # lo que hace falta para escribir un adaptador es el esquema real, y
        # adivinarlo es exactamente lo que este proyecto no hace.
        enlace = datos.get("datos", "")
        if not enlace:
            print("      sin enlace de datos en el sobre")
            continue

        try:
            with cliente() as c:
                d = c.get(enlace)
        except Exception as exc:
            print(f"      ✕ el enlace de datos no responde ({exc})")
            continue

        tipo = d.headers.get("content-type", "?")
        print(f"      {len(d.content)} bytes · {tipo}")
        _describir_carga(d.content, tipo)

    print("\n    Si las dos salen con ✓, mete la clave en Settings → Secrets and")
    print("    variables → Actions del repositorio, con el nombre AEMET_API_KEY,")
    print("    y pégame esta salida para que configure los adaptadores.")


def explorar() -> None:
    _titulo("Explorando candidatas conocidas")
    print("Recuerda: que una URL responda NO significa que sea la buena.")
    print("Hay que mirar qué devuelve.\n")
    for _source_id, urls in CANDIDATAS.items():
        for url in urls:
            sondear(url, source_id=None, guardar=False)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", help="URL a sondear (la que sacaste de DevTools)")
    p.add_argument("--id", help="source_id, para guardar el fixture (jcyl, infoca, ...)")
    p.add_argument("--listar", metavar="URL", help="enumerar capas de un ArcGIS/GeoServer")
    p.add_argument("--explorar", action="store_true", help="probar las candidatas conocidas")
    p.add_argument("--firms", action="store_true", help="comprobar FIRMS_MAP_KEY")
    p.add_argument("--aemet", action="store_true", help="comprobar AEMET_API_KEY")
    args = p.parse_args()

    if CONTACTO == "cambia-esto@ejemplo.org":
        print("⚠ Pon tu correo antes de lanzar peticiones a portales oficiales:")
        print("    export INCENDIOS_CONTACTO=tu@correo.es\n")

    if args.firms:
        comprobar_firms()
    elif args.aemet:
        comprobar_aemet()
    elif args.listar:
        listar_servicios(args.listar)
    elif args.url:
        sondear(args.url, source_id=args.id)
    elif args.explorar:
        explorar()
    else:
        p.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
