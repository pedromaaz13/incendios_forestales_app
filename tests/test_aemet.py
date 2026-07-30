"""Pruebas del adaptador de avisos CAP de AEMET.

El fixture `aemet_cap.xml` es la respuesta real del 29-07-2026: un aviso naranja
de temperaturas máximas en La Mancha albaceteña. Cuando AEMET cambie el formato,
estas pruebas dirán qué cambió antes de que el mapa empiece a mentir.
"""

from __future__ import annotations

import io
import tarfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from incendios import aemet

FIXTURE = Path(__file__).parent / "fixtures" / "aemet_cap.xml"


@pytest.fixture
def alerta() -> bytes:
    return FIXTURE.read_bytes()


def _tar(*ficheros: tuple[str, bytes]) -> bytes:
    """Empaqueta XMLs en un TAR sin comprimir, como lo sirve AEMET."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for nombre, contenido in ficheros:
            info = tarfile.TarInfo(nombre)
            info.size = len(contenido)
            tar.addfile(info, io.BytesIO(contenido))
    return buffer.getvalue()


# --- Lectura del esquema real ----------------------------------------------


def test_extrae_el_aviso_del_payload_real(alerta):
    filas = aemet.parse_alerta(alerta)

    assert len(filas) == 1, "las dos secciones <info> (es/en) deben dar un solo aviso"
    fila = filas[0]
    assert fila["fenomeno_codigo"] == "AT"
    assert fila["zona"] == "La Mancha albaceteña"
    assert fila["zona_codigo"] == "680201"


def test_el_nivel_sale_del_parametro_y_no_de_severity(alerta):
    """`severity` dice 'Severe'; el nivel que entiende la gente es 'naranja'.

    Publicar el vocabulario de CAP en vez del de Meteoalerta rompería el código
    de colores que la gente reconoce de los partes meteorológicos.
    """
    fila = aemet.parse_alerta(alerta)[0]

    assert fila["nivel"] == "naranja"
    assert fila["nivel_orden"] == 2
    assert "Severe" not in str(fila.values())


def test_solo_se_conserva_el_castellano(alerta):
    """Cada alerta trae es-ES y en-GB: sin filtrar, cada aviso saldría dos veces."""
    fila = aemet.parse_alerta(alerta)[0]

    assert "temperaturas máximas" in fila["titular"].lower()
    assert "high-temperature" not in fila["titular"].lower()


# --- Geometría: el fallo que no se ve --------------------------------------


def test_el_poligono_cae_en_espana_y_no_en_el_indico(alerta):
    """CAP escribe lat,lon y GeoJSON quiere lon,lat.

    Sin invertir, el polígono de Albacete (39 N, 2 O) se dibujaría en (2 N,
    39 E) — Somalia. El mapa seguiría pintando polígonos, así que el fallo no
    se detecta comprobando que "hay datos": hay que comprobar dónde caen.
    """
    geom = aemet.parse_alerta(alerta)[0]["geometry"]
    oeste, sur, este, _ = geom.bounds

    assert -3.0 < oeste < -1.0, f"longitud fuera de España: {oeste}"
    assert 38.0 < sur < 40.0, f"latitud fuera de España: {sur}"
    assert este < 0, "el polígono debería estar al oeste de Greenwich"


def test_un_poligono_degenerado_no_tumba_el_aviso(alerta):
    """Con menos de 4 vértices Shapely lanza; el aviso se omite sin propagar."""
    roto = alerta.decode("utf-8").replace(
        "<polygon>39.32,-2.74", "<polygon>39.32,-2.74 39.33,-2.63</polygon><polygon>"
    )
    filas = aemet.parse_alerta(roto.encode("utf-8"))

    assert filas == [] or all(f["geometry"] is not None for f in filas)


def test_xml_ilegible_devuelve_lista_vacia_sin_lanzar():
    assert aemet.parse_alerta(b"<alert>esto no cierra") == []


# --- Filtros: qué NO se publica --------------------------------------------


def test_los_avisos_verdes_no_se_publican(alerta):
    """Verde significa 'sin riesgo'. Pintarlo entierra los que sí importan."""
    verde = alerta.decode("utf-8").replace(
        "<value>naranja</value>", "<value>verde</value>"
    )
    assert aemet.parse_alerta(verde.encode("utf-8")) == []


def test_los_fenomenos_irrelevantes_se_descartan(alerta):
    """AEMET publica también aludes y avisos costeros: aquí son ruido."""
    costero = alerta.decode("utf-8").replace(
        "<value>AT;Temperaturas máximas</value>", "<value>CO;Costeros</value>"
    )
    assert aemet.parse_alerta(costero.encode("utf-8")) == []


@pytest.mark.parametrize("codigo", ["VI", "TO", "AT"])
def test_los_fenomenos_que_afectan_a_un_incendio_si_se_publican(alerta, codigo):
    """Viento, tormenta y calor son los tres que cambian cómo arde un monte."""
    mutado = alerta.decode("utf-8").replace(
        "<value>AT;Temperaturas máximas</value>", f"<value>{codigo};X</value>"
    )
    filas = aemet.parse_alerta(mutado.encode("utf-8"))

    assert len(filas) == 1
    assert filas[0]["fenomeno_codigo"] == codigo


# --- Desempaquetado del TAR ------------------------------------------------


def test_el_tar_se_lee_sin_comprimir(alerta):
    """AEMET sirve `application/x-gtar` sin gzip, al contrario de lo que sugiere
    el nombre habitual `.tar.gz`."""
    gdf = aemet.parse_tar(_tar(("aviso.xml", alerta)))

    assert len(gdf) == 1
    assert gdf.crs is not None


def test_un_xml_corrupto_no_pierde_los_demas(alerta, caplog):
    """Con 447 ficheros, perder los 446 buenos por uno malo es cambiar un fallo
    pequeño por uno grande, justo el día que más avisos se publican."""
    paquete = _tar(
        ("bueno.xml", alerta),
        ("roto.xml", b"\x00\x01 esto no es XML"),
    )
    gdf = aemet.parse_tar(paquete)

    assert len(gdf) == 1


def test_tar_vacio_devuelve_capa_vacia_con_esquema():
    """Una capa vacía sin columnas rompería el export aguas abajo."""
    gdf = aemet.parse_tar(_tar())

    assert gdf.empty
    assert "nivel" in gdf.columns
    assert "geometry" in gdf.columns


def test_se_queda_el_mas_grave_cuando_hay_reemision(alerta):
    """Misma zona y fenómeno dos veces: gana el nivel más alto.

    Mismo criterio que `_worst_status` en la fusión, y por la misma razón:
    quedarse corto es el error caro.
    """
    rojo = alerta.decode("utf-8").replace(
        "<value>naranja</value>", "<value>rojo</value>"
    ).encode("utf-8")
    gdf = aemet.parse_tar(_tar(("a.xml", alerta), ("b.xml", rojo)))

    assert len(gdf) == 1
    assert gdf["nivel"].iloc[0] == "rojo"


# --- Vigencia --------------------------------------------------------------


def test_los_avisos_expirados_se_descartan(alerta):
    """El boletín arrastra avisos de días anteriores. Uno de ayer mostrado como
    vigente es dato caducado publicado sin su edad."""
    gdf = aemet.parse_tar(_tar(("aviso.xml", alerta)))
    despues = datetime(2026, 8, 1, tzinfo=UTC)

    assert aemet.vigentes(gdf, ahora=despues).empty


def test_los_avisos_vigentes_se_conservan(alerta):
    gdf = aemet.parse_tar(_tar(("aviso.xml", alerta)))
    durante = datetime(2026, 7, 30, 14, tzinfo=UTC)

    assert len(aemet.vigentes(gdf, ahora=durante)) == 1


def test_vigentes_sobre_capa_vacia_no_lanza():
    assert aemet.vigentes(aemet.parse_tar(_tar())).empty
