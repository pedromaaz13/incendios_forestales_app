"""Agrupación en incendios y perímetros · RF-P-05, tabla 8.1 (filas 11-12).

Lo que este módulo tiene que acertar es el equilibrio: agrupar de más fusiona
dos incendios vecinos en uno y esconde el segundo; agrupar de menos devuelve la
nube de puntos que el clustering existe para evitar.

`fire_id` merece atención aparte. Se deriva de un hash del centroide y la fecha
de inicio, no de un autoincremental, para que el enlace permanente de un
incidente (RF-F-02) siga apuntando al mismo incendio en la ejecución siguiente.
"""

from __future__ import annotations

import geopandas as gpd
import pandas as pd
import pytest
from conftest import make_hotspots, to_gdf

from incendios import cluster as cluster_mod
from incendios.config import ACTIVE_WINDOW_HOURS, CLUSTER

GRADO_LAT_KM = 111.32


def _desplaza_km(lat: float, km: float) -> float:
    return lat + km / GRADO_LAT_KM


# --- tabla 8.1: incendio de un solo hotspot ---------------------------------


def test_single_hotspot_fire_is_valid(now):
    """`min_samples=1` está puesto a propósito: un incendio de un píxel es real.

    Un DBSCAN con min_samples=2 marcaría este punto como ruido y lo tiraría, que
    es precisamente el incendio pequeño que alguien busca cerca de su casa.
    """
    hotspots = to_gdf(make_hotspots(42.40, -7.85, n=1, frp=12.0))

    etiquetados = cluster_mod.assign_fire_ids(hotspots)
    fires = cluster_mod.build_fires(etiquetados, now=now)

    assert len(etiquetados) == 1
    assert len(fires) == 1
    assert fires["n_hotspots"].iloc[0] == 1
    assert fires["fire_id"].iloc[0]
    assert fires["status"].iloc[0] == "activo"
    assert fires.geometry.iloc[0].is_valid


def test_single_hotspot_gets_a_perimeter():
    """Sin hull posible, el perímetro es el propio píxel bufferizado."""
    hotspots = to_gdf(make_hotspots(42.40, -7.85, n=1))
    etiquetados = cluster_mod.assign_fire_ids(hotspots)

    perims = cluster_mod.build_perimeters(etiquetados)

    assert len(perims) == 1
    assert perims.geometry.iloc[0].is_valid
    assert perims["hull_area_ha"].iloc[0] > 0


# --- tabla 8.1: dos incendios a 4 km ----------------------------------------


def test_neighbouring_fires_4km_apart_do_not_merge():
    """4 km supera de largo el `eps_m` de 1500 m: son dos incendios."""
    a = make_hotspots(40.00, -6.00, n=5, spread_deg=0.002)
    b = make_hotspots(_desplaza_km(40.00, 4.0), -6.00, n=5, spread_deg=0.002)

    etiquetados = cluster_mod.assign_fire_ids(to_gdf(pd.concat([a, b], ignore_index=True)))

    assert etiquetados["fire_id"].nunique() == 2


def test_same_fire_across_two_passes_stays_one():
    """Dos pasadas del mismo incendio con 12 h de diferencia son un incendio.

    Es el caso opuesto al anterior y el que justifica el eje temporal escalado:
    con un DBSCAN puramente espacial esto también saldría 1, pero entonces dos
    incendios distintos en el mismo valle con una semana de diferencia también.
    """
    manana = make_hotspots(40.25, -6.60, n=8, spread_deg=0.004, hours_ago=14.0)
    tarde = make_hotspots(40.25, -6.60, n=8, spread_deg=0.004, hours_ago=2.0)

    etiquetados = cluster_mod.assign_fire_ids(
        to_gdf(pd.concat([manana, tarde], ignore_index=True))
    )

    assert etiquetados["fire_id"].nunique() == 1


def test_time_axis_separates_distant_passes():
    """Mismo sitio, 10 días de diferencia: no es el mismo incendio."""
    viejo = make_hotspots(40.25, -6.60, n=4, spread_deg=0.002, hours_ago=240.0)
    nuevo = make_hotspots(40.25, -6.60, n=4, spread_deg=0.002, hours_ago=1.0)

    etiquetados = cluster_mod.assign_fire_ids(
        to_gdf(pd.concat([viejo, nuevo], ignore_index=True))
    )

    assert etiquetados["fire_id"].nunique() == 2


@pytest.mark.skip(
    reason=(
        "RF-P-05 / RF-P-02 pendientes (hito 4). Falta la ingesta SEVIRI y el eps "
        "efectivo por sensor: hoy CLUSTER.eps_m es global (1500 m) y no escala "
        "con instrument. Escrito ahora pasaría en verde por accidente —a 4 km no "
        "se fusionan de todas formas— sin probar nada. Quitar el skip cuando "
        "exista instrument='SEVIRI' con precision_m=3000."
    )
)
def test_seviri_does_not_overmerge():
    """Dos incendios VIIRS a 4 km con un hotspot SEVIRI entre medias: siguen dos."""
    a = make_hotspots(40.00, -6.00, n=5, spread_deg=0.002)
    b = make_hotspots(_desplaza_km(40.00, 4.0), -6.00, n=5, spread_deg=0.002)
    puente = make_hotspots(
        _desplaza_km(40.00, 2.0), -6.00, n=1, instrument="SEVIRI", source="SEVIRI_FRP"
    )

    etiquetados = cluster_mod.assign_fire_ids(
        to_gdf(pd.concat([a, b, puente], ignore_index=True))
    )

    assert etiquetados["fire_id"].nunique() == 2


# --- estabilidad de fire_id -------------------------------------------------


def test_fire_id_is_stable_across_row_order():
    """RF-F-02 depende de esto: el enlace permanente no puede cambiar por que
    FIRMS devuelva las filas en otro orden."""
    hotspots = to_gdf(make_hotspots(40.25, -6.60, n=6, spread_deg=0.003))

    directo = cluster_mod.assign_fire_ids(hotspots)
    invertido = cluster_mod.assign_fire_ids(hotspots.iloc[::-1].copy())

    assert set(directo["fire_id"]) == set(invertido["fire_id"])


def test_fire_id_differs_between_distinct_fires():
    a = make_hotspots(40.00, -6.00, n=3, spread_deg=0.002)
    b = make_hotspots(42.40, -7.85, n=3, spread_deg=0.002)

    etiquetados = cluster_mod.assign_fire_ids(to_gdf(pd.concat([a, b], ignore_index=True)))

    assert etiquetados["fire_id"].nunique() == 2


def test_areas_are_clustered_independently():
    """Canarias se proyecta en un CRS distinto: mezclar bloques daría distancias
    absurdas entre un incendio en Tenerife y otro en Cáceres."""
    peninsula = make_hotspots(40.00, -6.00, n=3, spread_deg=0.002)
    canarias = make_hotspots(28.30, -16.50, n=3, spread_deg=0.002, area_key="canarias")

    etiquetados = cluster_mod.assign_fire_ids(
        to_gdf(pd.concat([peninsula, canarias], ignore_index=True))
    )

    assert etiquetados["fire_id"].nunique() == 2
    assert set(etiquetados["area_key"]) == {"peninsula", "canarias"}


# --- métricas agregadas -----------------------------------------------------


def test_build_fires_aggregates_frp_and_window(now):
    hotspots = to_gdf(
        make_hotspots(40.25, -6.60, n=4, spread_deg=0.003, frp=25.0, hours_ago=3.0)
    )
    etiquetados = cluster_mod.assign_fire_ids(hotspots)

    fires = cluster_mod.build_fires(etiquetados, now=now)

    fila = fires.iloc[0]
    assert fila["n_hotspots"] == 4
    assert fila["frp_total_mw"] == pytest.approx(100.0)
    assert fila["frp_max_mw"] == pytest.approx(25.0)
    assert fila["hours_since_last"] == pytest.approx(3.0, abs=0.01)
    assert fila["sensors"] == "VIIRS_NOAA20_NRT"


def test_fire_goes_inactive_after_the_active_window(now):
    """Pasadas ACTIVE_WINDOW_HOURS sin detección nueva deja de estar activo."""
    viejo = to_gdf(
        make_hotspots(40.25, -6.60, n=2, spread_deg=0.002, hours_ago=ACTIVE_WINDOW_HOURS + 6)
    )
    etiquetados = cluster_mod.assign_fire_ids(viejo)

    fires = cluster_mod.build_fires(etiquetados, now=now)

    assert fires["status"].iloc[0] == "inactivo"


@pytest.mark.parametrize(
    ("frp_por_hotspot", "n", "esperado"),
    [
        (5.0, 2, "baja"),      # 10 MW
        (30.0, 4, "media"),    # 120 MW
        (100.0, 4, "alta"),    # 400 MW
        (250.0, 8, "extrema"), # 2000 MW
    ],
)
def test_intensity_scale_derives_from_total_frp(frp_por_hotspot, n, esperado, now):
    """La rampa de intensidad alimenta el color del mapa (RF-F-03)."""
    hotspots = to_gdf(
        make_hotspots(40.25, -6.60, n=n, spread_deg=0.002, frp=frp_por_hotspot)
    )
    etiquetados = cluster_mod.assign_fire_ids(hotspots)

    fires = cluster_mod.build_fires(etiquetados, now=now)

    assert fires["intensity"].iloc[0] == esperado


def test_area_est_is_labelled_as_estimate_upstream(now):
    """`area_est_ha` es una cota inferior grosera (14,06 ha por píxel VIIRS).

    El nombre del campo ya lleva `_est`; el aviso de "estimación" es
    responsabilidad del frontend (RF-F-10), pero el número debe salir de aquí
    de forma determinista para que la ficha sea reproducible.
    """
    hotspots = to_gdf(make_hotspots(40.25, -6.60, n=10, spread_deg=0.003))
    etiquetados = cluster_mod.assign_fire_ids(hotspots)

    fires = cluster_mod.build_fires(etiquetados, now=now)

    assert fires["area_est_ha"].iloc[0] == pytest.approx(140.0, abs=1.0)


# --- perímetros -------------------------------------------------------------


def test_perimeter_per_fire():
    a = make_hotspots(40.00, -6.00, n=6, spread_deg=0.003)
    b = make_hotspots(42.40, -7.85, n=6, spread_deg=0.003)
    etiquetados = cluster_mod.assign_fire_ids(to_gdf(pd.concat([a, b], ignore_index=True)))

    perims = cluster_mod.build_perimeters(etiquetados)

    assert len(perims) == etiquetados["fire_id"].nunique()
    assert perims["fire_id"].is_unique
    assert perims.geometry.is_valid.all()
    assert perims.crs.to_epsg() == 4326


def test_build_perimeters_on_empty_input_returns_typed_frame():
    vacio = to_gdf(make_hotspots(40.0, -6.0, n=1)).iloc[0:0]
    vacio["fire_id"] = pd.Series(dtype=str)

    perims = cluster_mod.build_perimeters(vacio)

    assert perims.empty
    assert "fire_id" in perims.columns
    assert isinstance(perims, gpd.GeoDataFrame)


def test_cluster_params_keep_axes_comparable():
    """El eje temporal se escala a "metros equivalentes" con eps_m/eps_hours.

    Si alguien toca `eps_m` sin tocar `eps_hours`, la ventana temporal efectiva
    cambia en silencio. Esta comprobación deja constancia de la relación.
    """
    assert CLUSTER.time_scale_m_per_hour == pytest.approx(CLUSTER.eps_m / CLUSTER.eps_hours)
