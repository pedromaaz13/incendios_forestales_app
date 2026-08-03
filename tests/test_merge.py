"""Fusión oficial ↔ satélite · RF-P-06, tabla 8.1 (filas 13-16).

La regla central del módulo: **la tolerancia la fija la fuente menos precisa**.
Un punto de INFOCAM es el centroide del municipio (±6 km) y emparejarlo con
tolerancia de 500 m no encuentra nada; un punto de 112 CV es la coordenada del
incidente (±100 m) y emparejarlo con tolerancia de 6 km fusiona incendios
vecinos distintos.

Los dos errores tienen consecuencias opuestas y las dos son malas: no emparejar
duplica el incendio en el mapa, y emparejar de más hace desaparecer uno.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from conftest import make_fires, make_official

from incendios import merge
from incendios.merge import MATCH_MAX_M, MATCH_SLACK_M, MATCH_WINDOW_HOURS

GRADO_LAT_KM = 111.32


def _desplaza_km(lat: float, km: float) -> float:
    return lat + km / GRADO_LAT_KM


# --- tolerancia por fuente --------------------------------------------------


def test_tolerance_is_precision_plus_slack():
    tol = merge._tolerance_m(pd.Series([100.0, 500.0, 6000.0]))

    assert tol.tolist() == [
        100 + MATCH_SLACK_M,
        500 + MATCH_SLACK_M,
        6000 + MATCH_SLACK_M,
    ]


def test_tolerance_is_capped():
    """Sin tope, una fuente con precisión declarada absurda fusionaría media
    provincia en un solo incidente."""
    assert merge._tolerance_m(pd.Series([50_000.0])).iloc[0] == MATCH_MAX_M


# --- tabla 8.1: oficial ±6 km empareja con tolerancia amplia ----------------


def test_matches_infocam_at_6km(now):
    """RF-P-06: INFOCAM publica el centroide municipal. 6 km de error es normal
    y aun así es el mismo incendio."""
    official = make_official(
        [
            {
                "source_id": "infocam",
                "external_id": "CLM-1",
                "latitude": _desplaza_km(39.500, 6.0),
                "longitude": -2.500,
                "precision_m": 6000.0,
                "reported_at": now,
            }
        ]
    )
    fires = make_fires([{"fire_id": "f1", "latitude": 39.500, "longitude": -2.500}])

    emparejados, _ = merge.match(official, fires)

    assert emparejados["fire_id"].iloc[0] == "f1"
    assert emparejados["match_distance_m"].iloc[0] == pytest.approx(6000, abs=200)


def test_does_not_match_beyond_tolerance(now):
    """112 CV con ±100 m: a 6 km ya no es el mismo incendio, es el del valle de al lado."""
    official = make_official(
        [
            {
                "source_id": "112cv",
                "external_id": "CV-1",
                "latitude": _desplaza_km(39.500, 6.0),
                "longitude": -0.500,
                "precision_m": 100.0,
                "reported_at": now,
            }
        ]
    )
    fires = make_fires([{"fire_id": "f1", "latitude": 39.500, "longitude": -0.500}])

    emparejados, clusters = merge.match(official, fires)

    assert emparejados["fire_id"].isna().all()
    assert clusters["confirmed_by"].iloc[0] == ""


def test_does_not_match_outside_time_window(now):
    """Un parte de hace 3 días no confirma una detección de esta mañana."""
    official = make_official(
        [
            {
                "source_id": "jcyl",
                "external_id": "CYL-1",
                "latitude": 42.000,
                "longitude": -5.000,
                "reported_at": now - pd.Timedelta(hours=MATCH_WINDOW_HOURS + 24),
            }
        ]
    )
    fires = make_fires(
        [{"fire_id": "f1", "latitude": 42.000, "longitude": -5.000, "last_detected": now}]
    )

    emparejados, _ = merge.match(official, fires)

    assert emparejados["fire_id"].isna().all()


# --- tabla 8.1: dos oficiales a 800 m, un solo cluster ----------------------


def test_does_not_merge_neighbours(now):
    """Dos partes de 112 CV a 800 m con un solo cluster FIRMS cerca.

    Solo debe emparejarse el más próximo. El segundo no es un duplicado: es otro
    incendio que el satélite todavía no ha visto, y tiene que seguir apareciendo
    como huérfano oficial.
    """
    official = make_official(
        [
            {
                "source_id": "112cv",
                "external_id": "CV-A",
                "latitude": 39.5000,
                "longitude": -0.5000,
                "precision_m": 100.0,
                "reported_at": now,
            },
            {
                "source_id": "112cv",
                "external_id": "CV-B",
                "latitude": 39.5072,  # ~800 m al norte de CV-A
                "longitude": -0.5000,
                "precision_m": 100.0,
                "reported_at": now,
            },
        ]
    )
    fires = make_fires([{"fire_id": "f1", "latitude": 39.5010, "longitude": -0.5000}])

    emparejados, _ = merge.match(official, fires)

    assert int(emparejados["fire_id"].notna().sum()) == 1
    ganador = emparejados.loc[emparejados["fire_id"].notna(), "external_id"].iloc[0]
    assert ganador == "CV-A"


def test_loser_of_a_contested_cluster_survives_as_orphan(now):
    """La otra mitad del requisito: el parte que pierde el cluster no se pierde.

    Es la parte que importa de verdad. Descartar el emparejamiento sin más haría
    desaparecer el segundo incendio igual que antes, solo que por otra vía.
    """
    official = make_official(
        [
            {"external_id": "CV-A", "latitude": 39.5000, "longitude": -0.5,
             "precision_m": 100.0, "source_id": "112cv", "reported_at": now},
            {"external_id": "CV-B", "latitude": 39.5072, "longitude": -0.5,
             "precision_m": 100.0, "source_id": "112cv", "reported_at": now},
        ]
    )
    fires = make_fires([{"fire_id": "f1", "latitude": 39.5010, "longitude": -0.5}])

    emparejados, clusters = merge.match(official, fires)
    incidentes = merge.build_incidents(emparejados, clusters)

    # Dos incendios entran, dos incidentes salen: uno confirmado por satélite y
    # otro huérfano oficial. Ninguno desaparece.
    assert len(incidentes) == 2
    assert set(incidentes["origin"]) == {"ambos", "oficial"}
    assert "off_112cv_CV-B" in incidentes["fire_id"].tolist()


# --- tabla 8.1: estados discrepantes ----------------------------------------


def test_worst_status_wins_between_sources(now):
    """Si INFOCA dice 'controlado' y JCyL dice 'activo', se muestra 'activo'.

    Mostrar el estado más favorable es el error caro: alguien decide no salir de
    casa con esa información.
    """
    official = make_official(
        [
            {"source_id": "infoca", "external_id": "A", "latitude": 39.5005,
             "longitude": -0.5, "precision_m": 1500.0, "status": "controlado",
             "reported_at": now},
            {"source_id": "jcyl", "external_id": "B", "latitude": 39.4995,
             "longitude": -0.5, "precision_m": 500.0, "status": "activo",
             "reported_at": now},
        ]
    )
    fires = make_fires([{"fire_id": "f1", "latitude": 39.500, "longitude": -0.5}])

    _, clusters = merge.match(official, fires)

    assert clusters["official_status"].iloc[0] == "activo"


def test_two_reports_from_the_same_source_do_not_both_confirm(now):
    """El desempate es por fuente, y este test fija el porqué.

    Dos partes de 112 CV próximos son dos incendios: un servicio no notifica el
    mismo fuego dos veces. Dos partes de comunidades distintas próximos pueden
    ser el mismo frente en un límite provincial. La regla tiene que distinguir
    los dos casos, no aplicar el mismo criterio a ciegas.
    """
    misma_fuente = make_official(
        [
            {"source_id": "112cv", "external_id": "A", "latitude": 39.5000,
             "longitude": -0.5, "precision_m": 100.0, "reported_at": now},
            {"source_id": "112cv", "external_id": "B", "latitude": 39.5072,
             "longitude": -0.5, "precision_m": 100.0, "reported_at": now},
        ]
    )
    fires = make_fires([{"fire_id": "f1", "latitude": 39.5010, "longitude": -0.5}])

    emparejados, clusters = merge.match(misma_fuente, fires)

    assert int(emparejados["fire_id"].notna().sum()) == 1
    assert clusters["confirmed_by"].iloc[0] == "112cv"


def test_confirmed_by_lists_every_source(now):
    """RF-F-10 muestra quién confirma el incendio: se acumulan, no se pisan."""
    official = make_official(
        [
            {"source_id": "infoca", "external_id": "A", "latitude": 39.5005,
             "longitude": -0.5, "precision_m": 1500.0, "reported_at": now},
            {"source_id": "jcyl", "external_id": "B", "latitude": 39.4995,
             "longitude": -0.5, "precision_m": 500.0, "reported_at": now},
        ]
    )
    fires = make_fires([{"fire_id": "f1", "latitude": 39.500, "longitude": -0.5}])

    _, clusters = merge.match(official, fires)

    assert clusters["confirmed_by"].iloc[0] == "infoca,jcyl"


@pytest.mark.parametrize(
    ("estados", "esperado"),
    [
        (["controlado", "activo"], "activo"),
        (["extinguido", "controlado"], "controlado"),
        (["estabilizado", "controlado"], "estabilizado"),
        (["desconocido", "extinguido"], "extinguido"),
        (["activo", "estabilizado", "controlado"], "activo"),
    ],
)
def test_worst_status_ranking(estados, esperado):
    assert merge._worst_status(pd.Series(estados)) == esperado


def test_unknown_status_ranks_lowest():
    """Un estado no reconocido nunca debe ganar a uno real."""
    assert merge._worst_status(pd.Series(["desconocido", "controlado"])) == "controlado"


# --- tabla 8.1: oficial sin satélite se conserva como huérfano --------------


def test_orphan_official_is_preserved(now):
    """RF-P-06: un incendio oficial sin detección satelital sigue siendo real.

    Puede ser pequeño, o de noche bajo nubes. Descartarlo por no tener hotspot
    es exactamente lo contrario de lo que este proyecto hace.
    """
    official = make_official(
        [{"external_id": "SOLO", "latitude": 43.000, "longitude": -4.000, "reported_at": now}]
    )
    fires = make_fires([{"fire_id": "f1", "latitude": 39.500, "longitude": -0.500}])

    emparejados, clusters = merge.match(official, fires)
    incidentes = merge.build_incidents(emparejados, clusters)

    huerfano = incidentes[incidentes["origin"] == "oficial"]
    assert len(huerfano) == 1
    assert huerfano["satellite_confirmed"].iloc[0] is False or not huerfano[
        "satellite_confirmed"
    ].iloc[0]
    assert huerfano["confirmed_by"].iloc[0] == "jcyl"
    assert huerfano["fire_id"].iloc[0].startswith("off_")


def test_orphan_id_is_stable_and_namespaced(now):
    """El id del huérfano se deriva de source_id + external_id: estable entre
    ejecuciones y sin colisión posible con un fire_id de FIRMS."""
    official = make_official(
        [{"source_id": "112cv", "external_id": "42", "latitude": 39.0,
          "longitude": -0.4, "reported_at": now}]
    )
    fires = make_fires([{"fire_id": "f1", "latitude": 42.0, "longitude": -6.0}])

    emparejados, clusters = merge.match(official, fires)
    incidentes = merge.build_incidents(emparejados, clusters)

    ids = incidentes["fire_id"].tolist()
    assert "off_112cv_42" in ids
    assert len(set(ids)) == len(ids)


# --- origen del incidente ---------------------------------------------------


def test_origin_ambos_when_both_confirm(now):
    """Invariante 3 de la sección 4.4: origin == 'ambos' ⟺ los dos flags."""
    official = make_official(
        [{"external_id": "A", "latitude": 39.500, "longitude": -0.5, "reported_at": now}]
    )
    fires = make_fires([{"fire_id": "f1", "latitude": 39.500, "longitude": -0.5}])

    emparejados, clusters = merge.match(official, fires)
    incidentes = merge.build_incidents(emparejados, clusters)

    fila = incidentes[incidentes["fire_id"] == "f1"].iloc[0]
    assert fila["origin"] == "ambos"
    assert bool(fila["satellite_confirmed"]) is True


def test_cluster_status_is_translated_to_the_contract_vocabulary(now):
    """`build_fires` marca activo/inactivo y el contrato 4.3 no conoce "inactivo".

    Lo detectó la primera ejecución con datos reales: 173 incendios y el
    validador se negó a publicar, con razón.
    """
    official = make_official([]).iloc[0:0]
    fires = make_fires([{"fire_id": "f1", "latitude": 39.5, "longitude": -0.5}])
    fires["status"] = "activo"
    official, fires = merge.match(official, fires)

    incidentes = merge.build_incidents(official, fires)

    # `None` entra en el conjunto permitido desde que el estado solo se publica
    # cuando lo afirma un servicio de extinción. Lo que sigue prohibido, y es lo
    # que este test vigila, es que se escape el vocabulario interno del cluster.
    assert set(incidentes["status"]) <= {"activo", "estabilizado", "controlado", None}
    assert "inactivo" not in set(incidentes["status"])


def test_satellite_only_cluster_without_recent_detections_is_not_published(now):
    """Sin parte oficial y sin detección reciente no se publica como incidente.

    No sabemos si se apagó, si está bajo nube o si el satélite no ha vuelto a
    pasar. Llamarlo "controlado" sería inventarlo y llamarlo "activo" sería
    alarmar. Sus focos siguen en la capa de hotspots, que no afirma nada.
    """
    official = make_official([]).iloc[0:0]
    fires = make_fires([
        {"fire_id": "reciente", "latitude": 39.5, "longitude": -0.5},
        {"fire_id": "antiguo", "latitude": 40.5, "longitude": -5.5},
    ])
    fires["status"] = ["activo", "inactivo"]
    official, fires = merge.match(official, fires)

    incidentes = merge.build_incidents(official, fires)

    assert incidentes["id"].tolist() == ["reciente"]


def test_official_status_wins_over_the_cluster_window(now):
    """Con parte oficial manda el estado oficial: el satélite ve calor, no ve
    bomberos, así que solo la comunidad puede decir "controlado"."""
    official = make_official(
        [{"external_id": "A", "latitude": 39.5, "longitude": -0.5,
          "status": "controlado", "reported_at": now}]
    )
    fires = make_fires([{"fire_id": "f1", "latitude": 39.5, "longitude": -0.5}])
    # El cluster lleva sin detecciones más de la ventana activa...
    fires["status"] = "inactivo"

    emparejados, clusters = merge.match(official, fires)
    incidentes = merge.build_incidents(emparejados, clusters)

    # ...pero hay parte oficial, así que se publica con el estado que declara.
    fila = incidentes[incidentes["id"] == "f1"].iloc[0]
    assert fila["status"] == "controlado"


def test_origin_satelite_when_no_official(now):
    official = make_official([]).iloc[0:0]
    fires = make_fires([{"fire_id": "f1", "latitude": 39.500, "longitude": -0.5}])

    emparejados, clusters = merge.match(official, fires)
    incidentes = merge.build_incidents(emparejados, clusters)

    assert incidentes["origin"].tolist() == ["satelite"]


# --- casos degenerados ------------------------------------------------------


def test_match_with_no_officials_returns_untouched_clusters(now):
    fires = make_fires([{"fire_id": "f1", "latitude": 39.5, "longitude": -0.5}])

    emparejados, clusters = merge.match(make_official([]).iloc[0:0], fires)

    assert emparejados.empty
    assert clusters["confirmed_by"].tolist() == [""]
    assert "fire_id" in emparejados.columns


def test_match_with_no_fires_keeps_officials(now):
    official = make_official(
        [{"external_id": "A", "latitude": 39.5, "longitude": -0.5, "reported_at": now}]
    )
    fires = make_fires([{"fire_id": "f1", "latitude": 39.5, "longitude": -0.5}]).iloc[0:0]

    emparejados, clusters = merge.match(official, fires)

    assert len(emparejados) == 1
    assert emparejados["fire_id"].isna().all()
    assert np.isnan(emparejados["match_distance_m"].iloc[0])
    assert clusters.empty


# --- Precisión de la posición según el sensor -------------------------------
#
# El anillo punteado del mapa usa `position_precision_m`, y la ficha afirma
# sobre él que el incendio "puede estar en cualquier punto de su interior". Si
# el número es menor que la incertidumbre real, esa frase es falsa. Por eso hay
# tres pruebas y no una: el caso VIIRS, el caso MODIS y el mixto.


def test_precision_es_de_viirs_cuando_viirs_lo_vio(now):
    fires = make_fires([
        {"fire_id": "f1", "latitude": 40.0, "longitude": -3.0, "sensors": "VIIRS_SNPP_NRT"},
    ])
    emparejados, clusters = merge.match(make_official([]).iloc[0:0], fires)
    salida = merge.build_incidents(emparejados, clusters)
    assert salida["position_precision_m"].iloc[0] == merge.VIIRS_PIXEL_PRECISION_M


def test_precision_es_de_modis_cuando_solo_modis_lo_vio(now):
    """Un incendio visto solo por MODIS se conoce con 1 km, no con 375 m.

    Regresión del bug documentado en ESTADO-DEL-PROYECTO §5.1: la precisión se
    asignaba con una constante global, así que 8 de 44 incidentes en producción
    publicaban un anillo de incertidumbre tres veces menor que el real.
    """
    fires = make_fires([
        {"fire_id": "f1", "latitude": 40.0, "longitude": -3.0, "sensors": "MODIS_NRT"},
    ])
    emparejados, clusters = merge.match(make_official([]).iloc[0:0], fires)
    salida = merge.build_incidents(emparejados, clusters)
    assert salida["position_precision_m"].iloc[0] == merge.MODIS_PIXEL_PRECISION_M


def test_precision_toma_el_mejor_sensor_no_el_peor(now):
    """Con ambos sensores manda VIIRS: la detección más fina es la que acota."""
    fires = make_fires([
        {
            "fire_id": "f1",
            "latitude": 40.0,
            "longitude": -3.0,
            "sensors": "MODIS_NRT,VIIRS_NOAA20_NRT",
        },
    ])
    emparejados, clusters = merge.match(make_official([]).iloc[0:0], fires)
    salida = merge.build_incidents(emparejados, clusters)
    assert salida["position_precision_m"].iloc[0] == merge.VIIRS_PIXEL_PRECISION_M


# --- El estado solo lo afirma quien puede afirmarlo -------------------------
#
# Antes, un cluster sin parte oficial se publicaba con `status = "activo"` y la
# interfaz lo pintaba en rojo con esa palabra. Internamente solo significaba
# "detectado dentro de la ventana reciente": con 6 h de antigüedad y ninguna
# pasada posterior, ese incendio puede estar apagado.
#
# Afectaba al 100 % de lo publicado, porque hoy no hay ni un parte oficial en
# producción. Es exactamente el verbo de certeza que el aviso de dominio prohíbe.


def test_sin_parte_oficial_no_se_publica_ningun_estado(now):
    """Una detección de calor no dice si el fuego sigue vivo."""
    official = make_official([]).iloc[0:0]
    fires = make_fires([{"fire_id": "f1", "latitude": 39.5, "longitude": -0.5}])

    emparejados, clusters = merge.match(official, fires)
    fila = merge.build_incidents(emparejados, clusters).iloc[0]

    assert fila["status"] is None
    assert fila["status_origen"] == "satelite"


def test_con_parte_oficial_el_estado_es_el_que_declara_la_fuente(now):
    official = make_official([
        {"source_id": "jcyl", "latitude": 39.500, "longitude": -0.5,
         "precision_m": 500.0, "status": "controlado"},
    ])
    fires = make_fires([{"fire_id": "f1", "latitude": 39.500, "longitude": -0.5}])

    emparejados, clusters = merge.match(official, fires)
    fila = merge.build_incidents(emparejados, clusters).iloc[0]

    assert fila["status"] == "controlado"
    assert fila["status_origen"] == "oficial"


def test_un_huerfano_oficial_sin_estado_declarado_queda_nulo(now):
    """Ser un parte oficial no basta: si la fuente no declara estado, no se
    inventa uno. Antes se rellenaba con "activo"."""
    official = make_official([
        {"source_id": "112cv", "latitude": 38.0, "longitude": -1.0,
         "precision_m": 100.0, "status": None},
    ])
    fires = make_fires([{"fire_id": "f1", "latitude": 42.0, "longitude": -6.0}])

    emparejados, clusters = merge.match(official, fires)
    incidentes = merge.build_incidents(emparejados, clusters)
    huerfano = incidentes[incidentes["origin"] == "oficial"].iloc[0]

    assert huerfano["status"] is None or pd.isna(huerfano["status"])
    assert huerfano["status_origen"] == "oficial"


# --- Lo que solo sabe el parte oficial --------------------------------------
#
# `igr_level` y los medios estaban en el contrato 4.3 desde el principio, el
# frontend los pintaba, y **nadie los rellenaba nunca**: `match` propagaba solo
# el estado, el nombre y `confirmed_by`. No se notó porque el generador de
# demostración los ponía a mano después, así que la demo enseñaba «Nivel IGR 2 ·
# 16 aéreos» y producción publicaba los dos campos nulos.
#
# Un dato que solo existe en la demo es peor que no tenerlo: parece que funciona.


def test_el_nivel_del_parte_oficial_llega_al_incidente(now):
    official = make_official([
        {"source_id": "jcyl", "latitude": 39.5, "longitude": -0.5,
         "precision_m": 500.0, "status": "activo", "level": 2},
    ])
    fires = make_fires([{"fire_id": "f1", "latitude": 39.5, "longitude": -0.5}])

    emparejados, clusters = merge.match(official, fires)
    fila = merge.build_incidents(emparejados, clusters).iloc[0]

    assert fila["igr_level"] == 2


def test_los_medios_del_parte_oficial_llegan_al_incidente(now):
    official = make_official([
        {"source_id": "jcyl", "latitude": 39.5, "longitude": -0.5,
         "precision_m": 500.0, "status": "activo",
         "resources": "16 aéreos · 80 terrestres"},
    ])
    fires = make_fires([{"fire_id": "f1", "latitude": 39.5, "longitude": -0.5}])

    emparejados, clusters = merge.match(official, fires)
    fila = merge.build_incidents(emparejados, clusters).iloc[0]

    assert fila["resources_text"] == "16 aéreos · 80 terrestres"


def test_con_dos_fuentes_gana_el_nivel_mas_alto(now):
    """Mismo criterio que `_worst_status`: quedarse corto es el error caro."""
    official = make_official([
        {"source_id": "jcyl", "latitude": 39.500, "longitude": -0.500,
         "precision_m": 500.0, "status": "activo", "level": 1},
        {"source_id": "infocam", "latitude": 39.501, "longitude": -0.501,
         "precision_m": 500.0, "status": "activo", "level": 2},
    ])
    fires = make_fires([{"fire_id": "f1", "latitude": 39.5, "longitude": -0.5}])

    emparejados, clusters = merge.match(official, fires)
    fila = merge.build_incidents(emparejados, clusters).iloc[0]

    assert fila["igr_level"] == 2


def test_con_dos_fuentes_los_medios_se_suman_sin_repetir(now):
    """Dos comunidades que confirman el mismo frente despliegan cada una los
    suyos, y el texto debe reflejar las dos."""
    official = make_official([
        {"source_id": "jcyl", "latitude": 39.500, "longitude": -0.500,
         "precision_m": 500.0, "status": "activo", "resources": "4 autobombas"},
        {"source_id": "infocam", "latitude": 39.501, "longitude": -0.501,
         "precision_m": 500.0, "status": "activo", "resources": "2 helicópteros"},
    ])
    fires = make_fires([{"fire_id": "f1", "latitude": 39.5, "longitude": -0.5}])

    emparejados, clusters = merge.match(official, fires)
    texto = merge.build_incidents(emparejados, clusters).iloc[0]["resources_text"]

    assert "4 autobombas" in texto
    assert "2 helicópteros" in texto


def test_un_huerfano_oficial_tambien_publica_nivel_y_medios(now):
    """Sin detección satelital el parte sigue siendo un parte: su nivel y sus
    medios son igual de válidos."""
    official = make_official([
        {"source_id": "112cv", "latitude": 38.0, "longitude": -1.0,
         "precision_m": 100.0, "status": "activo", "level": 1,
         "resources": "1 aéreo · 8 terrestres"},
    ])
    fires = make_fires([{"fire_id": "f1", "latitude": 42.0, "longitude": -6.0}])

    emparejados, clusters = merge.match(official, fires)
    incidentes = merge.build_incidents(emparejados, clusters)
    huerfano = incidentes[incidentes["origin"] == "oficial"].iloc[0]

    assert huerfano["igr_level"] == 1
    assert huerfano["resources_text"] == "1 aéreo · 8 terrestres"


def test_sin_parte_oficial_el_nivel_y_los_medios_quedan_nulos(now):
    """Un satélite no despliega bomberos ni declara niveles."""
    official = make_official([]).iloc[0:0]
    fires = make_fires([{"fire_id": "f1", "latitude": 39.5, "longitude": -0.5}])

    emparejados, clusters = merge.match(official, fires)
    fila = merge.build_incidents(emparejados, clusters).iloc[0]

    assert pd.isna(fila["igr_level"])
    assert not fila["resources_text"]


def test_un_campo_ausente_no_publica_la_palabra_nan(now):
    """`str(NaN)` es `"nan"`, que es una cadena no vacía y se colaba tal cual.

    Salió en la demo como «Dónde: nan» en cinco incendios de seis. Es peor que
    no poner nada: parece un dato y no lo es.
    """
    official = make_official([
        {"source_id": "jcyl", "latitude": 39.5, "longitude": -0.5,
         "precision_m": 500.0, "status": "activo", "detalle": None, "resources": None},
    ])
    fires = make_fires([{"fire_id": "f1", "latitude": 39.5, "longitude": -0.5}])

    emparejados, clusters = merge.match(official, fires)
    fila = merge.build_incidents(emparejados, clusters).iloc[0]

    assert fila["detalle_oficial"] in (None, ""), f"salió {fila['detalle_oficial']!r}"
    assert str(fila["resources_text"]) != "nan"


def test_el_detalle_del_parte_llega_al_incidente(now):
    """La dirección con las palabras del operador es lo único que aporta el 112
    valenciano y no se puede derivar de una coordenada."""
    official = make_official([
        {"source_id": "112cv", "latitude": 39.5, "longitude": -0.5,
         "precision_m": 100.0, "status": "activo",
         "detalle": "CV-223 Km4, a mano derecha"},
    ])
    fires = make_fires([{"fire_id": "f1", "latitude": 39.5, "longitude": -0.5}])

    emparejados, clusters = merge.match(official, fires)
    fila = merge.build_incidents(emparejados, clusters).iloc[0]

    assert fila["detalle_oficial"] == "CV-223 Km4, a mano derecha"
