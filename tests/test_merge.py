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


@pytest.mark.xfail(
    strict=True,
    reason=(
        "RF-P-06 exige este test por nombre y hoy falla. sjoin_nearest va de "
        "oficial -> incendio, así que cada oficial busca su cluster más próximo "
        "de forma independiente y nada impide que dos elijan el mismo. Resultado: "
        "dos incendios distintos colapsan en un incidente y uno desaparece del "
        "mapa. Falta desempate por distancia dentro de cada fire_id. "
        "Requiere tocar src/incendios/merge.py — pendiente de aprobación."
    ),
)
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


def test_two_officials_currently_share_one_cluster(now):
    """Contrapartida del anterior: deja constancia del comportamiento de hoy.

    Sin este test, arreglar `merge.py` volvería verde el `xfail` de arriba sin
    que nadie viese qué cambió exactamente.
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

    emparejados, _ = merge.match(official, fires)

    assert int(emparejados["fire_id"].notna().sum()) == 2


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
