"""Pruebas del parseo de INFORCYL · Junta de Castilla y León.

El fixture es la respuesta real capturada el 30-07-2026: el incendio de
Burgohondo (Ávila), activo desde el 22 de julio.

La mayoría de estas pruebas existen por **una** característica del esquema: los
campos se llaman `latitud` y `longitud` pero contienen metros UTM. Es el tipo de
trampa que produce un mapa que funciona y una comunidad que nunca aparece.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from incendios.sources import jcyl

FIXTURE = Path(__file__).parent / "fixtures" / "jcyl.json"


@pytest.fixture
def payload() -> dict:
    """Las tres emergencias reales del fixture: un IGR 2 activo, un controlado
    y una falsa alarma."""
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def uno(payload) -> dict:
    """Solo el incendio de Burgohondo, para las pruebas que lo corrompen.

    Con las tres, mutar la primera y comprobar que la salida está vacía no
    probaría nada: las otras dos seguirían saliendo.
    """
    return {"listaEmergencias": [payload["listaEmergencias"][0]]}


# --- Coordenadas: la trampa del esquema -------------------------------------


def test_las_coordenadas_utm_acaban_en_castilla_y_leon(payload):
    """`latitud: 4468904` es un northing, no 4.468.904 grados.

    Sin convertir, el punto se sale del planeta y esa comunidad no aparece nunca
    en el mapa. El mapa, mientras tanto, sigue funcionando: es el fallo
    silencioso que este proyecto vigila.
    """
    fila = jcyl.extraer(payload)[0]

    assert 40.0 < fila["latitude"] < 43.5, f"latitud fuera de la comunidad: {fila['latitude']}"
    assert -7.2 < fila["longitude"] < -1.5, f"longitud fuera: {fila['longitude']}"


def test_la_coordenada_es_la_del_incendio_y_no_la_del_pueblo(payload):
    """Burgohondo está en (40,409 O 4,782) y el foco sale a ~6 km al sureste.

    Importa porque justifica el `precision_m` de 500 m: si la fuente publicara
    el centroide del municipio, la tolerancia de fusión tendría que ser de
    kilómetros, como en INFOCAM.
    """
    fila = jcyl.extraer(payload)[0]

    assert fila["latitude"] == pytest.approx(40.358, abs=0.01)
    assert fila["longitude"] == pytest.approx(-4.738, abs=0.01)


def test_un_huso_desconocido_descarta_el_registro_sin_lanzar(uno):
    uno["listaEmergencias"][0]["huso"] = 99

    assert jcyl.extraer(uno) == []


def test_un_cambio_de_sistema_de_coordenadas_se_detecta(uno):
    """Si la Junta empezara a publicar en grados, la transformación **no falla**:
    devuelve un punto válido en mitad del Atlántico. El recuadro lo caza."""
    uno["listaEmergencias"][0]["latitud"] = 40.41
    uno["listaEmergencias"][0]["longitud"] = -4.78

    assert jcyl.extraer(uno) == []


def test_coordenadas_invertidas_se_detectan(uno):
    """Intercambiar northing y easting da un punto fuera de la comunidad."""
    fila = uno["listaEmergencias"][0]
    fila["latitud"], fila["longitud"] = fila["longitud"], fila["latitud"]

    assert jcyl.extraer(uno) == []


# --- Campos del contrato ----------------------------------------------------


def test_extrae_el_incendio_del_payload_real(payload):
    fila = jcyl.extraer(payload)[0]

    assert fila["external_id"] == "5-152-26"
    assert fila["status"] == "activo"
    assert fila["raw_status"] == "Activo"
    assert fila["municipio"] == "Burgohondo"


def test_el_identificador_combina_las_tres_partes(payload):
    """No hay campo `id`: la emergencia se identifica por la terna
    (cpm, num1, num2). Usar una sola colisionaría entre provincias."""
    otro = json.loads(json.dumps(payload))
    otro["listaEmergencias"][0]["emergencia_cpm"] = 9      # Burgos, mismo num1/num2

    assert jcyl.extraer(payload)[0]["external_id"] != jcyl.extraer(otro)[0]["external_id"]


def test_la_fecha_espanola_se_convierte_a_utc(payload):
    """`22/07/2026 13:02:00` es hora peninsular: en UTC son las 11:02.

    Publicarla sin convertir adelantaría los incendios dos horas en verano, y eso
    desplaza la ventana de emparejamiento de 48 h con las detecciones de FIRMS.
    """
    fila = jcyl.extraer(payload)[0]

    assert fila["reported_at"] == pd.Timestamp("2026-07-22T11:02:00Z")


def test_una_fecha_ilegible_no_tumba_el_registro(payload):
    payload["listaEmergencias"][0]["fecha_inicio"] = "el martes"

    fila = jcyl.extraer(payload)[0]

    assert fila["reported_at"] is None
    assert fila["status"] == "activo", "el resto del registro debe conservarse"


def test_un_estado_nuevo_no_se_traduce_a_algo_plausible(payload):
    """Si la Junta añade un estado, se marca `desconocido` en vez de adivinar.

    Traducir "Sofocado" a "controlado" por parecido sería inventar una
    afirmación sobre un incendio real.
    """
    payload["listaEmergencias"][0]["estado"]["NOMBRE"] = "Sofocado"

    fila = jcyl.extraer(payload)[0]

    assert fila["status"] == "desconocido"
    assert fila["raw_status"] == "Sofocado", "el original se conserva para depurar"


# --- Medios -----------------------------------------------------------------


def test_solo_se_cuentan_los_medios_que_estan_actuando(payload):
    """El listado trae también los que NO actúan: medios del dispositivo que no
    están en este incendio. Contarlos todos multiplicaría la cifra."""
    emergencia = payload["listaEmergencias"][0]
    actuando = sum(1 for m in emergencia["medios"] if m["ACTUANDO"])
    assert actuando < len(emergencia["medios"]), "el fixture debe tener de los dos tipos"

    resumen = jcyl.extraer(payload)[0]["resources"]

    assert sum(int(t.split()[0]) for t in resumen.split(" · ")) == actuando


def test_sin_medios_actuando_el_resumen_queda_vacio(payload):
    for medio in payload["listaEmergencias"][0]["medios"]:
        medio["ACTUANDO"] = False

    assert jcyl.extraer(payload)[0]["resources"] == ""


def test_un_registro_sin_medios_no_lanza(payload):
    del payload["listaEmergencias"][0]["medios"]

    assert jcyl.extraer(payload)[0]["resources"] == ""


# --- Filtros ----------------------------------------------------------------


def test_las_falsas_alarmas_no_se_publican(uno):
    """La propia Junta las marca como tales: publicarlas sería contradecir a la
    fuente y alarmar por un incendio que no existe."""
    uno["listaEmergencias"][0]["falsa_alarma"] = True

    assert jcyl.extraer(uno) == []


def test_una_respuesta_vacia_devuelve_lista_vacia():
    assert jcyl.extraer({"listaEmergencias": []}) == []


def test_una_respuesta_sin_la_clave_esperada_no_lanza():
    """Si la Junta renombra `listaEmergencias`, el adaptador devuelve vacío y la
    fuente se marca sin datos, en vez de reventar el pipeline entero."""
    assert jcyl.extraer({"otraCosa": [{"foo": 1}]}) == []


# --- Lo que aporta la fuente que no teníamos --------------------------------


def test_publica_el_nivel_operativo_del_plan_infocal(payload):
    """`nivel_infocal` 0-2 es lo que la Junta enseña como «IGR» en su visor.

    No confundir con `nivelIgr`, que es un booleano y solo dice si la escala le
    aplica: usar ese daría `True`/`False` donde el contrato espera un número.
    """
    por_id = {f["external_id"]: f for f in jcyl.extraer(payload)}

    assert por_id["5-152-26"]["level"] == 2, "Burgohondo es un IGR 2"
    assert por_id["9-304-26"]["level"] == 0


def test_publica_municipio_y_provincia_en_nombre_propio(payload):
    """La fuente escribe en mayúsculas y `.title()` dejaría «Cuevas De San
    Clemente»: en castellano las preposiciones van en minúscula."""
    por_id = {f["external_id"]: f for f in jcyl.extraer(payload)}

    assert por_id["9-304-26"]["municipio"] == "Cuevas de San Clemente"
    assert por_id["9-304-26"]["provincia"] == "Burgos"


def test_las_tres_emergencias_reales_dan_dos_publicables(payload):
    """La tercera es una falsa alarma declarada por la Junta."""
    filas = jcyl.extraer(payload)

    assert len(filas) == 2
    assert {f["status"] for f in filas} == {"activo", "controlado"}


def test_las_provincias_recuperan_su_tilde(payload):
    """La fuente publica «AVILA» y «LEON» sin tilde.

    Son nueve y es un vocabulario cerrado, así que se corrigen con una tabla.
    Los municipios no: son más de 2.000 y restituir tildes por reglas no
    funciona en topónimos.
    """
    assert jcyl._provincia("AVILA") == "Ávila"
    assert jcyl._provincia("LEON") == "León"
    assert jcyl._provincia("BURGOS") == "Burgos"


def test_una_provincia_desconocida_no_se_pierde():
    """Si la Junta publicara una provincia fuera de la tabla, se formatea igual
    en vez de devolver nulo: perder el dato sería peor que perder la tilde."""
    assert jcyl._provincia("CACERES") == "Caceres"


def test_el_guion_tambien_separa_nombre_propio():
    """`str.title()` solo mira los espacios y dejaría «Villaverde-mogina»."""
    assert jcyl._nombre_propio("VILLAVERDE-MOGINA") == "Villaverde-Mogina"
