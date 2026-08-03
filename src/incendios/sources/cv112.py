"""Parseo del 112 · Comunitat Valenciana.

Endpoint descubierto el 31-07-2026 siguiendo la cadena de scripts del visor:
`incendios.js` monta una vista que declara sus rutas, y una de ellas es

    https://wpr.112cv.gva.es/external/api/storage/descargar/json/incidentes

Es público: responde 200 con un User-Agent identificable, sin cookie ni cabecera
de sesión. El fixture está en `tests/fixtures/112cv.json`.

**Es un feed de incidencias del 112, no de incendios.** De 58 registros, 15 eran
incendios; el resto accidentes, contaminación, salvamentos y suministros. Filtrar
es lo primero que hace este módulo, y filtrar mal publicaría un accidente de
tráfico como incendio forestal.

Lo que aporta que ninguna otra fuente da: la **dirección en texto libre** que
escribe el operador —«AP-7 Km364 >sur», «CV-794 Km7 Bocairent > Alcoi, a mano
derecha»— que sitúa el fuego respecto a una carretera, que es como la gente
localiza las cosas.

Lo que **no** trae, y condiciona todo lo demás: **no hay fecha ni hora**. Ningún
campo la lleva. Se asume que el feed son las incidencias vigentes ahora, así que
`reported_at` queda nulo y `base.py` lo rellena con el instante de la ejecución.
Eso significa que la ventana de emparejamiento de 48 h con FIRMS se mide desde
que lo vimos nosotros, no desde que empezó el incendio.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# Prefijo que marca un incendio en la taxonomía del 112. Sus descripciones son
# jerárquicas: «Incendio > Vegetación > Forestal».
PREFIJO_INCENDIO = "incendio"

# Ramas de vegetación que se publican. `Urbana` se excluye a propósito: un
# incendio de vegetación urbana —un solar, una mediana— no es lo que este visor
# cubre, y mezclarlo inflaría el recuento con sucesos que no preocupan a nadie
# que mire si arde el monte.
#
# «Rural/Montañosa Humo» es una categoría suya distinta de «Rural/Montañosa»:
# significa que se ha avisado de humo, no de llama confirmada. Se conserva porque
# un aviso de humo en monte es exactamente lo que este visor debe enseñar pronto,
# pero se marca aparte para que la ficha pueda decirlo.
RAMAS_PUBLICABLES = ("vegetación > forestal", "vegetación > rural/montañosa")
RAMA_EXCLUIDA = "vegetación > urbana"

# Recuadro de la Comunitat Valenciana con margen. Igual que en JCyL: no es un
# filtro geográfico, es la comprobación de que las coordenadas son grados y no
# otra cosa. Aquí llegan ya en grados, pero un cambio de formato en origen se
# manifestaría como un punto plausible en otro sitio.
BBOX = (-1.6, 37.8, 0.8, 40.9)

# Valor centinela que usa el 112 para incidencias fuera de su territorio: no es
# un municipio y publicarlo tal cual pondría «Fuera de la Comunidad Valenciana»
# como nombre del incendio en la lista. Se deja nulo y el geocoding inverso
# contra la capa del IGN pone el que corresponda.
MUNICIPIO_CENTINELA = "fuera de la comunidad valenciana"


def _coordenadas(fila: dict) -> tuple[float, float] | None:
    """Primera coordenada del incidente, validada contra el recuadro.

    `coordenadas` es una lista: un incidente puede tener varias localizaciones
    —un incendio con dos frentes, un corte con dos extremos— y se toma la
    primera. No se promedian: el punto medio entre dos frentes puede caer en un
    sitio donde no hay nada ardiendo.
    """
    puntos = fila.get("coordenadas") or []
    if not puntos:
        return None

    primero = puntos[0] or {}
    lon, lat = primero.get("x"), primero.get("y")
    if lon is None or lat is None:
        return None

    oeste, sur, este, norte = BBOX
    if not (oeste <= lon <= este and sur <= lat <= norte):
        log.warning(
            "112cv: (%.4f, %.4f) cae fuera de la Comunitat Valenciana. "
            "¿Ha cambiado el sistema de coordenadas?", lat, lon,
        )
        return None

    return float(lat), float(lon)


def _municipio(valor: str | None) -> str | None:
    """Descarta el centinela de fuera de territorio."""
    if not valor or valor.strip().lower() == MUNICIPIO_CENTINELA:
        return None
    return valor.strip()


def _es_incendio_publicable(descripcion: str) -> bool:
    """Distingue un incendio de vegetación del resto de incidencias del 112."""
    d = (descripcion or "").strip().lower()
    if not d.startswith(PREFIJO_INCENDIO):
        return False
    if RAMA_EXCLUIDA in d:
        return False
    return any(rama in d for rama in RAMAS_PUBLICABLES)


def _solo_humo(descripcion: str) -> bool:
    """La fuente distingue el aviso de humo de la llama confirmada."""
    return "humo" in (descripcion or "").lower()


def extraer(bruto: list) -> list[dict]:
    """Convierte el feed de incidencias en filas del contrato oficial.

    El feed no es de incendios: hay que filtrarlo. De los 58 registros del día
    en que se descubrió, 15 eran incendios y solo 14 de vegetación no urbana.
    """
    if not isinstance(bruto, list):
        log.warning("112cv: se esperaba una lista y llegó %s", type(bruto).__name__)
        return []

    filas: list[dict] = []
    descartados = {"no_incendio": 0, "sin_coordenadas": 0}

    for incidente in bruto:
        descripcion = incidente.get("descripcionEs") or ""

        if not _es_incendio_publicable(descripcion):
            descartados["no_incendio"] += 1
            continue

        punto = _coordenadas(incidente)
        if punto is None:
            descartados["sin_coordenadas"] += 1
            continue

        lat, lon = punto
        humo = _solo_humo(descripcion)

        filas.append({
            "external_id": str(incidente.get("id") or ""),
            "latitude": lat,
            "longitude": lon,
            # Sin fecha en origen. `base.py` lo rellena con el instante de la
            # ejecución, que es lo único defendible: el feed son las incidencias
            # vigentes ahora.
            "reported_at": None,
            # El 112 no publica estado de extinción, solo que la incidencia está
            # abierta. `activo` es lo que eso significa y no se infiere más.
            "status": "activo",
            "raw_status": descripcion,
            "municipio": _municipio(incidente.get("municipio")),
            "provincia": None,   # no viene; el geocoding inverso la pondrá
            "level": None,       # el 112 no publica escala de gravedad
            # La dirección en texto libre del operador, que es lo que esta fuente
            # aporta y ninguna otra da. Se marca cuando es solo aviso de humo,
            # porque no es lo mismo que llama confirmada.
            "resources": "",
            "detalle": (incidente.get("direccion") or "").strip() or None,
            "solo_humo": humo,
        })

    if any(descartados.values()):
        log.info(
            "112cv: %d incidencias que no son incendio de vegetación y %d sin "
            "coordenadas, descartadas",
            descartados["no_incendio"], descartados["sin_coordenadas"],
        )
    log.info("112cv: %d incendios publicables", len(filas))
    return filas
