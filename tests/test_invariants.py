"""Los ocho invariantes de la sección 4.4 · RF-P-14.

Un caso corrupto por invariante, cada uno verificando que **aborta la
publicación**. Es la última puerta antes de que un dato llegue a alguien que
está mirando si arde algo cerca de su casa: si algo no cuadra, es preferible que
el frontend siga mostrando la ejecución anterior con su edad real —que crecerá a
la vista— que publicar datos corruptos con marca de tiempo fresca.
"""

from __future__ import annotations

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

from incendios import validate

NOW = pd.Timestamp("2026-07-27T18:00:00Z")


def _incidente(**overrides) -> dict:
    """Incidente válido según el contrato 4.3. Los tests lo corrompen de uno en uno."""
    base = {
        "id": "abc123",
        "origin": "satelite",
        "satellite_confirmed": True,
        "official_confirmed": False,
        "confirmed_by": "",
        # Sin parte oficial no hay estado: una detección satelital dice que hubo
        # calor, no que el fuego siga vivo (invariante 9).
        "status": None,
        "status_origen": "satelite",
        "municipio": "Burgohondo",
        "provincia": "Ávila",
        "n_hotspots": 12,
        "frp_total_mw": 340.0,
        "intensity": "alta",
        "area_est_ha": 168.0,
        "position_precision_m": 375.0,
        "first_detected": NOW - pd.Timedelta(hours=6),
        "last_detected": NOW,
        "started_at": None,
        "geometry": Point(-4.78, 40.41),
    }
    return {**base, **overrides}


def _gdf(*incidentes: dict) -> gpd.GeoDataFrame:
    filas = list(incidentes) or [_incidente()]
    return gpd.GeoDataFrame(pd.DataFrame(filas), geometry="geometry", crs=4326)


# --- referencia: un fichero correcto pasa ------------------------------------


def test_valid_file_passes():
    """Sin esto, un `check` que devolviese siempre violaciones parecería correcto."""
    assert validate.check(_gdf()) == []
    validate.validate_or_abort(_gdf())  # no lanza


def test_empty_file_is_not_an_invariant_violation():
    """Cero incidentes es un caso legítimo (madrugada de febrero).

    Que la salida esté vacía por un fallo de fuente lo detecta RF-P-11, que es
    otra comprobación distinta: aquí solo se validan los invariantes.
    """
    assert validate.check(_gdf().iloc[0:0]) == []


# --- 1 · id único ------------------------------------------------------------


def test_invariant_1_duplicate_id_aborts():
    corrupto = _gdf(_incidente(id="dup"), _incidente(id="dup"))

    violaciones = validate.check(corrupto)

    assert [v.invariant for v in violaciones] == [1]
    with pytest.raises(SystemExit):
        validate.validate_or_abort(corrupto)


# --- 2 · no hay incidentes sin origen ---------------------------------------


def test_invariant_2_incident_without_origin_aborts():
    corrupto = _gdf(
        _incidente(satellite_confirmed=False, official_confirmed=False, origin="satelite")
    )

    violaciones = validate.check(corrupto)

    assert 2 in [v.invariant for v in violaciones]
    with pytest.raises(SystemExit):
        validate.validate_or_abort(corrupto)


# --- 3 · origin == 'ambos' <=> los dos flags --------------------------------


def test_invariant_3_origin_ambos_without_both_flags_aborts():
    corrupto = _gdf(_incidente(origin="ambos", official_confirmed=False))

    violaciones = validate.check(corrupto)

    assert 3 in [v.invariant for v in violaciones]
    with pytest.raises(SystemExit):
        validate.validate_or_abort(corrupto)


def test_invariant_3_both_flags_without_origin_ambos_aborts():
    """La equivalencia va en los dos sentidos."""
    corrupto = _gdf(_incidente(origin="satelite", official_confirmed=True))

    assert 3 in [v.invariant for v in validate.check(corrupto)]


def test_invariant_3_unknown_origin_aborts():
    corrupto = _gdf(_incidente(origin="satélite"))  # con tilde: no es del vocabulario

    assert 3 in [v.invariant for v in validate.check(corrupto)]


# --- 4 · first_detected <= last_detected ------------------------------------


def test_invariant_4_inverted_time_window_aborts():
    corrupto = _gdf(
        _incidente(first_detected=NOW, last_detected=NOW - pd.Timedelta(hours=6))
    )

    violaciones = validate.check(corrupto)

    assert 4 in [v.invariant for v in violaciones]
    with pytest.raises(SystemExit):
        validate.validate_or_abort(corrupto)


# --- 5 · geometría dentro de España -----------------------------------------


def test_invariant_5_geometry_outside_spain_aborts():
    corrupto = _gdf(_incidente(geometry=Point(2.35, 48.86)))  # París

    violaciones = validate.check(corrupto)

    assert 5 in [v.invariant for v in violaciones]
    with pytest.raises(SystemExit):
        validate.validate_or_abort(corrupto)


def test_invariant_5_canarias_is_inside_spain():
    """El bbox tiene que incluir Canarias o se descartaría media comunidad."""
    canarias = _gdf(_incidente(geometry=Point(-16.50, 28.30)))

    assert validate.check(canarias) == []


def test_invariant_5_zero_zero_is_caught():
    """(0, 0) es el fallo clásico de un campo de coordenada a nulo."""
    corrupto = _gdf(_incidente(geometry=Point(0.0, 0.0)))

    assert 5 in [v.invariant for v in validate.check(corrupto)]


# --- 6 · position_precision_m > 0 -------------------------------------------


@pytest.mark.parametrize("valor", [0.0, -100.0, None])
def test_invariant_6_non_positive_precision_aborts(valor):
    """Sin precisión positiva no se puede dibujar el anillo de RF-F-03."""
    corrupto = _gdf(_incidente(position_precision_m=valor))

    violaciones = validate.check(corrupto)

    assert 6 in [v.invariant for v in violaciones]
    with pytest.raises(SystemExit):
        validate.validate_or_abort(corrupto)


# --- 7 · n_hotspots == 0 implica origin == 'oficial' ------------------------


def test_invariant_7_satellite_incident_without_hotspots_aborts():
    corrupto = _gdf(_incidente(n_hotspots=0, origin="satelite"))

    violaciones = validate.check(corrupto)

    assert 7 in [v.invariant for v in violaciones]
    with pytest.raises(SystemExit):
        validate.validate_or_abort(corrupto)


def test_invariant_7_official_orphan_without_hotspots_is_valid():
    """El huérfano oficial con 0 hotspots es el caso legítimo, no una violación."""
    huerfano = _gdf(
        _incidente(
            id="off_112cv_42",
            origin="oficial",
            satellite_confirmed=False,
            official_confirmed=True,
            confirmed_by="112cv",
            n_hotspots=0,
            position_precision_m=100.0,
        )
    )

    assert validate.check(huerfano) == []


# --- 8 · extinguido no se publica -------------------------------------------


def test_invariant_8_extinguished_incident_aborts():
    corrupto = _gdf(_incidente(status="extinguido"))

    violaciones = validate.check(corrupto)

    assert 8 in [v.invariant for v in violaciones]
    with pytest.raises(SystemExit):
        validate.validate_or_abort(corrupto)


def test_invariant_8_unknown_status_aborts():
    corrupto = _gdf(_incidente(status="apagándose"))

    assert 8 in [v.invariant for v in validate.check(corrupto)]


# --- esquema incompleto ------------------------------------------------------


def test_missing_contract_columns_abort_before_anything_else():
    """Si falta media tabla, el diagnóstico útil es ese, no ocho violaciones."""
    incompleto = gpd.GeoDataFrame(
        pd.DataFrame([{"fire_id": "x", "geometry": Point(-4.78, 40.41)}]),
        geometry="geometry",
        crs=4326,
    )

    violaciones = validate.check(incompleto)

    assert len(violaciones) == 1
    assert violaciones[0].invariant == 0
    assert "id" in violaciones[0].detail


# --- mensajes de error -------------------------------------------------------


def test_violation_message_names_the_offending_ids():
    """El log tiene que decir qué incidente falla, no solo que algo falla."""
    corrupto = _gdf(_incidente(id="culpable", status="extinguido"))

    mensaje = str(validate.check(corrupto)[0])

    assert "invariante 8" in mensaje
    assert "culpable" in mensaje


def test_abort_reports_every_violation_not_just_the_first():
    """Arreglar de uno en uno con el cron cada 10 min es una tarde perdida."""
    corrupto = _gdf(
        _incidente(id="dup", status="extinguido", position_precision_m=0.0),
        _incidente(id="dup"),
    )

    violaciones = validate.check(corrupto)

    assert {v.invariant for v in violaciones} >= {1, 6, 8}


# --- Invariante 9 · ningún estado sin quien lo afirme -----------------------


def test_invariant_9_estado_sin_parte_oficial_aborta():
    """El fallo de más alcance que ha tenido este proyecto.

    Los 79 incendios de producción se publicaban con `status = "activo"` y la
    interfaz los pintaba en rojo con esa palabra, sin que ningún servicio de
    extinción lo hubiera declarado. Afectaba al 100 % de los datos.
    """
    incidents = _gdf(_incidente(status="activo", official_confirmed=False))

    violaciones = validate.check(incidents)

    assert any(v.invariant == 9 for v in violaciones)


def test_invariant_9_con_parte_oficial_el_estado_es_valido():
    incidents = _gdf(
        _incidente(status="controlado", official_confirmed=True, origin="ambos")
    )

    assert not [v for v in validate.check(incidents) if v.invariant == 9]


def test_invariant_9_el_nulo_es_valido_y_no_dispara_el_vocabulario():
    """Nulo significa «nadie lo ha declarado» y es el caso mayoritario hoy.

    Si el filtro del vocabulario del invariante 8 no excluyera los nulos, cada
    incendio satelital dispararía una violación y no se publicaría nada.
    """
    incidents = _gdf(_incidente(status=None, official_confirmed=False))

    violaciones = validate.check(incidents)

    assert not [v for v in violaciones if v.invariant in (8, 9)]
