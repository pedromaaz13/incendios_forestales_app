"""Parseo de INFORCYL · Junta de Castilla y León.

Esquema verificado contra la respuesta real de `servicios.jcyl.es/.../emergencias`
capturada el 30-07-2026 con las DevTools. El fixture está en
`tests/fixtures/jcyl.json`.

Vive en su propio módulo y no en una lambda dentro de `adapters.py` porque tiene
tres conversiones que necesitan explicación, y una de ellas es la más peligrosa
que ha aparecido en este proyecto.

**Las coordenadas no son grados: son metros UTM.**

    "huso": 30, "latitud": 4468904, "longitud": 352454

Los nombres de campo dicen `latitud` y `longitud`, pero son el *northing* y el
*easting* del huso 30 (EPSG:25830). Burgohondo está realmente en 40,41 N y
4,78 O. Usarlos tal cual pondría los incendios de Castilla y León a 4.468.904
grados de latitud, y el fallo es de los que no avisan: el mapa seguiría
funcionando y esa comunidad simplemente no aparecería nunca.

Por eso `_a_wgs84` valida el resultado contra el recuadro de la comunidad en vez
de confiar en la transformación.
"""

from __future__ import annotations

import logging

import pandas as pd
from pyproj import Transformer

log = logging.getLogger(__name__)

# Husos UTM que cubren Castilla y León. La comunidad está a caballo entre el 29
# y el 30, y el propio registro declara cuál usa en `huso`.
_TRANSFORMADORES = {
    29: Transformer.from_crs("EPSG:25829", "EPSG:4326", always_xy=True),
    30: Transformer.from_crs("EPSG:25830", "EPSG:4326", always_xy=True),
    31: Transformer.from_crs("EPSG:25831", "EPSG:4326", always_xy=True),
}
HUSO_POR_DEFECTO = 30

# Recuadro generoso de Castilla y León, con margen para incendios en el límite
# con comunidades vecinas. No es un filtro geográfico: es la comprobación de que
# la transformación ha hecho algo con sentido.
BBOX = (-7.2, 40.0, -1.5, 43.5)

# El estado viene en castellano y ya casi coincide con el vocabulario del
# contrato 4.3. Se mapea explícitamente para que un valor nuevo de la fuente
# —"Nivel 2", "Sofocado"— no se traduzca solo a algo plausible y equivocado.
ESTADOS = {
    "activo": "activo",
    # Aparece como valor de `estado` además del booleano `falsa_alarma`. Estos
    # registros se descartan antes de llegar aquí, pero reconocerlo evita que un
    # cambio en el booleano los cuele como "desconocido".
    "falsa alarma": "falsa_alarma",
    "estabilizado": "estabilizado",
    "controlado": "controlado",
    "extinguido": "extinguido",
}


def _a_wgs84(x: float, y: float, huso: int) -> tuple[float, float] | None:
    """De UTM ETRS89 a grados. Devuelve `None` si el resultado cae fuera.

    La validación no sobra. Si la Junta cambia el huso, invierte los campos o
    empieza a publicar en grados, la transformación no falla: devuelve un punto
    perfectamente válido en Marruecos o en el Atlántico. Comprobar el recuadro
    es lo que convierte ese silencio en un registro y un descarte.
    """
    transformador = _TRANSFORMADORES.get(int(huso or HUSO_POR_DEFECTO))
    if transformador is None:
        log.warning("JCyL: huso %r desconocido, se descarta el registro", huso)
        return None

    lon, lat = transformador.transform(x, y)

    oeste, sur, este, norte = BBOX
    if not (oeste <= lon <= este and sur <= lat <= norte):
        log.warning(
            "JCyL: (%.0f, %.0f) huso %s cae en (%.4f, %.4f), fuera de Castilla y "
            "León. ¿Han cambiado el sistema de coordenadas?",
            x, y, huso, lat, lon,
        )
        return None

    return lat, lon


def _identificador(fila: dict) -> str:
    """Identificador estable a partir de las tres partes que publica la fuente.

    No hay un campo `id`: la emergencia se identifica por la terna
    (`emergencia_cpm`, `emergencia_num1`, `emergencia_num2`). Usar solo una de
    las tres colisionaría entre provincias.
    """
    partes = (fila.get("emergencia_cpm"), fila.get("emergencia_num1"), fila.get("emergencia_num2"))
    return "-".join(str(p) for p in partes if p is not None)


def _medios(fila: dict) -> str:
    """Resumen de los medios **que están actuando**, por categoría.

    El listado trae también los que no actúan —`ACTUANDO: false`, medios del
    dispositivo que no están en este incendio— y contarlos todos multiplicaría
    por tres la cifra que ve la gente. En el ejemplo capturado, 96 medios
    listados y 55 actuando.
    """
    actuando = [m for m in fila.get("medios") or [] if m.get("ACTUANDO")]
    if not actuando:
        return ""

    por_tipo: dict[str, int] = {}
    for medio in actuando:
        nombre = ((medio.get("TIPO") or {}).get("NOMBRE") or "Otros").strip()
        por_tipo[nombre] = por_tipo.get(nombre, 0) + 1

    return " · ".join(
        f"{n} {tipo.lower()}" for tipo, n in sorted(por_tipo.items(), key=lambda kv: -kv[1])
    )


# La fuente publica las provincias en mayúsculas y **sin tildes**: «AVILA»,
# «LEON». Son nueve y es un vocabulario cerrado, así que se corrigen a mano en
# vez de intentar restituir tildes por reglas, que en topónimos no funciona.
#
# Los municipios se quedan como vengan: son más de 2.000 y no hay forma
# fiable de saber si «Peñaranda» lleva tilde sin una tabla que no tenemos.
PROVINCIAS = {
    "avila": "Ávila",
    "leon": "León",
    "burgos": "Burgos",
    "palencia": "Palencia",
    "salamanca": "Salamanca",
    "segovia": "Segovia",
    "soria": "Soria",
    "valladolid": "Valladolid",
    "zamora": "Zamora",
}

# Partículas que van en minúscula dentro de un topónimo castellano.
_MENORES = {"de", "del", "la", "las", "el", "los", "y", "e", "a", "al"}


def _nombre_propio(valor: str | None) -> str | None:
    """`CUEVAS DE SAN CLEMENTE` → `Cuevas de San Clemente`.

    `str.title()` a secas dejaría «Cuevas De San Clemente» y
    «Villaverde-Mogina» perdería la mayúscula tras el guion, porque `title()`
    solo mira los espacios.
    """
    if not valor:
        return None

    def capitaliza(palabra: str, primera: bool) -> str:
        if not primera and palabra in _MENORES:
            return palabra
        # Los guiones separan nombre propio: «Villaverde-Mogina», no «-mogina».
        return "-".join(t.capitalize() for t in palabra.split("-"))

    palabras = valor.strip().lower().split()
    return " ".join(
        capitaliza(p, i == 0) for i, p in enumerate(palabras)
    ) or None


def _provincia(valor: str | None) -> str | None:
    """Nombre de provincia con su tilde, desde el vocabulario cerrado."""
    if not valor:
        return None
    return PROVINCIAS.get(valor.strip().lower(), _nombre_propio(valor))


def _fecha(valor: str | None) -> pd.Timestamp | None:
    """`22/07/2026 13:02:00` → UTC.

    Formato español explícito, no inferido: con `dayfirst` automático, un
    `05/07` se interpretaría como 7 de mayo la mitad de las veces.

    La hora es local peninsular y se convierte a UTC, que es lo que el pipeline
    usa en todo momento. Publicarla sin convertir adelantaría los incendios dos
    horas en verano, y eso desplaza la ventana de emparejamiento con FIRMS.
    """
    if not valor:
        return None
    marca = pd.to_datetime(valor, format="%d/%m/%Y %H:%M:%S", errors="coerce")
    if pd.isna(marca):
        log.warning("JCyL: fecha ilegible %r", valor)
        return None
    return marca.tz_localize("Europe/Madrid", ambiguous=True).tz_convert("UTC")


def extraer(bruto: dict) -> list[dict]:
    """Convierte la respuesta de INFORCYL en filas del contrato oficial.

    Se descartan aquí, y no aguas abajo, dos casos:

    - **Falsas alarmas** (`falsa_alarma: true`). La propia Junta las marca como
      tales; publicarlas sería contradecir a la fuente.
    - **Coordenadas que no superan la validación**, con su aviso en el registro.
    """
    filas: list[dict] = []
    descartadas = {"falsa_alarma": 0, "coordenadas": 0}

    for emergencia in bruto.get("listaEmergencias") or []:
        if emergencia.get("falsa_alarma"):
            descartadas["falsa_alarma"] += 1
            continue

        punto = _a_wgs84(
            emergencia.get("longitud"),   # easting  → x
            emergencia.get("latitud"),    # northing → y
            emergencia.get("huso"),
        )
        if punto is None:
            descartadas["coordenadas"] += 1
            continue

        lat, lon = punto
        estado_bruto = (emergencia.get("estado") or {}).get("NOMBRE") or ""

        # `municipio` y `provincia` están al primer nivel; `localidad` repite el
        # municipio y añade la entidad menor. Se prefiere el primero y el
        # segundo queda de reserva por si algún registro solo trae uno.
        localidad = emergencia.get("localidad") or {}
        municipio = (
            (emergencia.get("municipio") or {}).get("nombre")
            or (localidad.get("municipio") or {}).get("nombre")
            or localidad.get("nombre")
        )
        provincia = (emergencia.get("provincia") or {}).get("nombre")

        filas.append({
            "external_id": _identificador(emergencia),
            "latitude": lat,
            "longitude": lon,
            "reported_at": _fecha(emergencia.get("fecha_inicio")),
            "status": ESTADOS.get(estado_bruto.strip().lower(), "desconocido"),
            "raw_status": estado_bruto,
            # La fuente escribe en mayúsculas: «BURGOHONDO» gritado en la ficha
            # de un incendio queda agresivo y además desentona con el resto.
            "municipio": _nombre_propio(municipio),
            "provincia": _provincia(provincia),
            # `nivel_infocal` es la situación operativa 0-2 del plan INFOCAL, que
            # es lo que la interfaz de la Junta enseña como «IGR». `nivelIgr` es
            # un booleano aparte que solo dice si le aplica la escala.
            "level": emergencia.get("nivel_infocal"),
            "resources": _medios(emergencia),
        })

    if any(descartadas.values()):
        log.info(
            "JCyL: %d falsas alarmas y %d con coordenadas no válidas descartadas",
            descartadas["falsa_alarma"], descartadas["coordenadas"],
        )
    log.info("JCyL: %d emergencias publicables", len(filas))
    return filas
