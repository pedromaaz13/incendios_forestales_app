"""Pruebas del contexto por incendio: viento, avisos y cortes.

Nada de esto añade fuentes: cruza capas que ya se publican. Por eso las pruebas
se centran en los dos modos de fallo propios de un cruce —el convenio de la
dirección y el radio de vecindad— más los casos degenerados, que es donde un
cruce de capas se rompe en silencio.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point, Polygon

from incendios import contexto
from incendios.config import CRS_WGS84


def _incendios(*puntos: tuple[float, float]) -> gpd.GeoDataFrame:
    """Incidentes mínimos: lo único que el contexto necesita es la geometría."""
    return gpd.GeoDataFrame(
        {"id": [f"f{i}" for i in range(len(puntos))]},
        geometry=[Point(lon, lat) for lat, lon in puntos],
        crs=CRS_WGS84,
    )


def _viento(*nodos: tuple[float, float, float, float]) -> gpd.GeoDataFrame:
    """Nodos de viento: (lat, lon, hacia_deg, velocidad_kmh)."""
    return gpd.GeoDataFrame(
        {
            "direction_to_deg": [n[2] for n in nodos],
            "speed_kmh": [n[3] for n in nodos],
            "gusts_kmh": [n[3] * 1.5 for n in nodos],
            "temp_c": [30.0] * len(nodos),
            "humedad_pct": [40.0] * len(nodos),
        },
        geometry=[Point(n[1], n[0]) for n in nodos],
        crs=CRS_WGS84,
    )


# --- Viento -----------------------------------------------------------------


def test_interpola_el_viento_del_nodo_mas_cercano():
    inc = _incendios((40.0, -3.0))
    # Nodo pegado al incendio y otro lejos con viento opuesto: manda el cercano.
    v = _viento((40.01, -3.01, 90.0, 40.0), (36.0, 3.0, 270.0, 5.0))

    fila = contexto.anadir_viento(inc, v).iloc[0]

    assert 80 < fila["viento_hacia_deg"] < 100
    assert fila["viento_kmh"] > 30


def test_el_cardinal_es_de_origen_y_los_grados_de_destino():
    """El error clásico de esta capa es mezclar los dos convenios.

    Un viento que sopla **hacia** el sur (180°) es el «viento del norte» en
    castellano. Si se publicaran los dos con el mismo sentido, la ficha diría
    que el viento del sur sopla hacia el sur.
    """
    inc = _incendios((40.0, -3.0))
    v = _viento((40.0, -3.0, 180.0, 20.0))

    fila = contexto.anadir_viento(inc, v).iloc[0]

    assert fila["viento_hacia_deg"] == pytest.approx(180.0, abs=1)
    assert fila["viento_cardinal_desde"] == "N"


def test_el_rumbo_se_promedia_como_vector_no_como_numero():
    """Entre 350° y 10° la media aritmética da 180°: el sentido contrario.

    Dos nodos equidistantes a un lado y otro del norte deben dar norte, no sur.
    Es el fallo que produciría una ficha diciendo que el viento sopla hacia el
    sur cuando sopla hacia el norte, sin que nada parezca roto.
    """
    inc = _incendios((40.0, -3.0))
    v = _viento((40.1, -3.0, 350.0, 20.0), (39.9, -3.0, 10.0, 20.0))

    hacia = contexto.anadir_viento(inc, v).iloc[0]["viento_hacia_deg"]

    assert hacia > 340 or hacia < 20, f"promedio circular mal calculado: {hacia}"


def test_un_incendio_lejos_de_la_malla_no_recibe_viento_inventado():
    """La rejilla tiene un nodo cada ~80 km. Más allá del umbral no se interpola.

    Publicar un viento extrapolado a 500 km del nodo más próximo sería
    inventarlo, y un nulo es un "no se sabe" honesto que la ficha puede omitir.
    """
    inc = _incendios((40.0, -3.0))
    v = _viento((28.0, -16.0, 90.0, 30.0))  # Canarias

    fila = contexto.anadir_viento(inc, v).iloc[0]

    assert pd.isna(fila["viento_kmh"])
    assert pd.isna(fila["viento_hacia_deg"])


def test_sin_capa_de_viento_las_columnas_existen_y_son_nulas():
    """Una columna ausente y una nula no son lo mismo en GeoJSON: el frontend
    lee estos nombres, y ausente es un campo que intenta leer y no encuentra."""
    salida = contexto.anadir_viento(_incendios((40.0, -3.0)), None)

    for campo in contexto.CAMPOS_CONTEXTO:
        assert campo in salida.columns
    assert pd.isna(salida.iloc[0]["viento_kmh"])


def test_nodos_sin_direccion_no_tumban_la_interpolacion():
    inc = _incendios((40.0, -3.0))
    v = _viento((40.0, -3.0, 90.0, 20.0))
    v.loc[0, "direction_to_deg"] = np.nan

    fila = contexto.anadir_viento(inc, v).iloc[0]

    assert pd.isna(fila["viento_hacia_deg"])


# --- Avisos -----------------------------------------------------------------


def _avisos(*items: tuple[str, int, str, tuple[float, float]]) -> gpd.GeoDataFrame:
    """Avisos cuadrados de 1° alrededor de un centro (lat, lon)."""
    geoms = []
    for _, _, _, (lat, lon) in items:
        geoms.append(Polygon([
            (lon - 0.5, lat - 0.5), (lon + 0.5, lat - 0.5),
            (lon + 0.5, lat + 0.5), (lon - 0.5, lat + 0.5),
        ]))
    return gpd.GeoDataFrame(
        {
            "nivel": [i[0] for i in items],
            "nivel_orden": [i[1] for i in items],
            "fenomeno": [i[2] for i in items],
            "titular": [f"Aviso de {i[2]}" for i in items],
        },
        geometry=geoms,
        crs=CRS_WGS84,
    )


def test_marca_el_aviso_que_cubre_el_incendio():
    inc = _incendios((40.0, -3.0))
    av = _avisos(("naranja", 2, "viento", (40.0, -3.0)))

    fila = contexto.anadir_avisos(inc, av).iloc[0]

    assert fila["aviso_nivel"] == "naranja"
    assert fila["aviso_fenomeno"] == "viento"


def test_con_varios_avisos_gana_el_mas_grave():
    """Calor y viento a la vez es lo habitual en julio. Quedarse con el menor
    sería el error caro, igual que en `_worst_status` de la fusión."""
    inc = _incendios((40.0, -3.0))
    av = _avisos(
        ("amarillo", 1, "temperaturas máximas", (40.0, -3.0)),
        ("rojo", 3, "viento", (40.0, -3.0)),
    )

    salida = contexto.anadir_avisos(inc, av)

    assert len(salida) == 1, "el incendio no debe duplicarse por estar en dos avisos"
    assert salida.iloc[0]["aviso_nivel"] == "rojo"


def test_un_incendio_fuera_de_todo_aviso_queda_nulo():
    inc = _incendios((40.0, -3.0))
    av = _avisos(("rojo", 3, "viento", (37.0, -6.0)))

    assert pd.isna(contexto.anadir_avisos(inc, av).iloc[0]["aviso_nivel"])


def test_sin_avisos_no_lanza():
    salida = contexto.anadir_avisos(_incendios((40.0, -3.0)), None)
    assert pd.isna(salida.iloc[0]["aviso_nivel"])


# --- Cortes de carretera ----------------------------------------------------


def _cortes(*items: tuple[float, float, bool]) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"por_incendio": [i[2] for i in items]},
        geometry=[Point(i[1], i[0]) for i in items],
        crs=CRS_WGS84,
    )


def test_cuenta_los_cortes_dentro_del_radio():
    inc = _incendios((40.0, -3.0))
    c = _cortes(
        (40.02, -3.02, True),    # ~3 km
        (40.05, -3.05, False),   # ~6 km
        (41.5, -3.0, True),      # ~165 km, fuera
    )

    fila = contexto.anadir_cortes(inc, c).iloc[0]

    assert fila["cortes_cerca"] == 2
    assert fila["cortes_cerca_por_incendio"] == 1


def test_la_causa_no_se_deduce_de_la_proximidad():
    """Un corte por obras a 3 km de un fuego sigue siendo un corte por obras.

    La causa es un dato que declara la DGT, nunca algo inferido por cercanía.
    Este test fija que el contador de "por incendio" no se contamine.
    """
    inc = _incendios((40.0, -3.0))
    c = _cortes((40.01, -3.01, False), (40.02, -3.02, False))

    fila = contexto.anadir_cortes(inc, c).iloc[0]

    assert fila["cortes_cerca"] == 2
    assert fila["cortes_cerca_por_incendio"] == 0


def test_sin_cortes_cerca_se_publica_cero_y_no_nulo():
    """Cero es una afirmación comprobada —se miró y no había—; nulo sería "no se
    sabe". La ficha los dice distinto."""
    inc = _incendios((40.0, -3.0))
    c = _cortes((41.5, -3.0, True))

    assert contexto.anadir_cortes(inc, c).iloc[0]["cortes_cerca"] == 0


def test_sin_capa_de_trafico_queda_nulo_no_cero():
    """Aquí sí es nulo: sin la capa no se ha podido mirar."""
    salida = contexto.anadir_cortes(_incendios((40.0, -3.0)), None)
    assert pd.isna(salida.iloc[0]["cortes_cerca"])


# --- Conjunto ---------------------------------------------------------------


def test_enriquecer_aplica_los_tres_cruces():
    inc = _incendios((40.0, -3.0))
    salida = contexto.enriquecer(
        inc,
        viento=_viento((40.0, -3.0, 180.0, 25.0)),
        avisos=_avisos(("naranja", 2, "viento", (40.0, -3.0))),
        cortes=_cortes((40.02, -3.02, True)),
    )
    fila = salida.iloc[0]

    assert fila["viento_kmh"] == pytest.approx(25.0, abs=0.5)
    assert fila["aviso_nivel"] == "naranja"
    assert fila["cortes_cerca"] == 1


def test_enriquecer_sin_ninguna_capa_de_contexto():
    """Es el caso de un pipeline con `--sin-viento --sin-avisos --sin-trafico`,
    y también el de las tres fuentes caídas a la vez."""
    salida = contexto.enriquecer(_incendios((40.0, -3.0)))

    assert len(salida) == 1
    for campo in contexto.CAMPOS_CONTEXTO:
        assert campo in salida.columns


def test_sin_incendios_no_lanza():
    vacio = gpd.GeoDataFrame({"id": []}, geometry=[], crs=CRS_WGS84)
    assert contexto.enriquecer(vacio, viento=_viento((40.0, -3.0, 90.0, 10.0))).empty


# --- Ritmo de crecimiento ---------------------------------------------------


def _hotspots(*items: tuple[str, float]) -> gpd.GeoDataFrame:
    """(fire_id, horas_atras) por foco."""
    ahora = pd.Timestamp("2026-07-30T12:00:00Z")
    return gpd.GeoDataFrame(
        {
            "fire_id": [i[0] for i in items],
            "acq_dt": [ahora - pd.Timedelta(hours=i[1]) for i in items],
        },
        geometry=[Point(-3.0, 40.0)] * len(items),
        crs=CRS_WGS84,
    )


AHORA = pd.Timestamp("2026-07-30T12:00:00Z")


def test_cuenta_solo_los_focos_de_la_ventana_reciente():
    """Un foco de hace 20 h no describe el ritmo de ahora."""
    inc = _incendios((40.0, -3.0))
    inc["id"] = ["f0"]
    hs = _hotspots(("f0", 1.0), ("f0", 3.0), ("f0", 20.0))

    fila = contexto.anadir_ritmo(inc, hs, ahora=AHORA).iloc[0]

    assert fila["focos_recientes"] == 2


def test_el_ritmo_usa_la_misma_constante_que_la_superficie():
    """Si usara otra, el crecimiento publicado no cuadraría con el área
    publicada y no habría forma de saber cuál de las dos miente."""
    inc = _incendios((40.0, -3.0))
    inc["id"] = ["f0"]
    hs = _hotspots(("f0", 1.0), ("f0", 2.0), ("f0", 3.0))

    fila = contexto.anadir_ritmo(inc, hs, ahora=AHORA).iloc[0]

    esperado = round(3 * contexto.AREA_POR_FOCO_HA / contexto.VENTANA_RITMO_H, 1)
    assert fila["crecimiento_ha_h"] == esperado


def test_un_incendio_sin_focos_recientes_publica_cero():
    """Cero focos recientes es un dato, y la ficha lo matiza: puede estar
    apagado, bajo nube, o sin pasada. Por eso se publican los dos números."""
    inc = _incendios((40.0, -3.0))
    inc["id"] = ["f0"]
    hs = _hotspots(("f0", 30.0))

    fila = contexto.anadir_ritmo(inc, hs, ahora=AHORA).iloc[0]

    assert fila["focos_recientes"] == 0
    assert fila["crecimiento_ha_h"] == 0.0


def test_el_ritmo_no_mezcla_focos_de_incendios_distintos():
    inc = _incendios((40.0, -3.0), (41.0, -4.0))
    inc["id"] = ["f0", "f1"]
    hs = _hotspots(("f0", 1.0), ("f1", 1.0), ("f1", 2.0), ("f1", 3.0))

    salida = contexto.anadir_ritmo(inc, hs, ahora=AHORA)

    assert salida.set_index("id")["focos_recientes"].to_dict() == {"f0": 1, "f1": 3}


def test_sin_hotspots_el_ritmo_queda_nulo_no_cero():
    """Sin la capa no se ha podido mirar; cero afirmaría que no ha crecido."""
    inc = _incendios((40.0, -3.0))
    inc["id"] = ["f0"]

    fila = contexto.anadir_ritmo(inc, None).iloc[0]

    assert pd.isna(fila["focos_recientes"])


# --- Distancia al núcleo de población ---------------------------------------
#
# Es la única línea de la aplicación que responde literalmente a la pregunta con
# la que se entra: ¿arde algo cerca de mi casa? Y por eso es también la más
# sensible a una imprecisión.


def _nucleos(*items: tuple[str, float, float, int]) -> gpd.GeoDataFrame:
    """(nombre, lat, lon, habitantes)."""
    return gpd.GeoDataFrame(
        {"nombre": [i[0] for i in items], "habitantes": [i[3] for i in items]},
        geometry=[Point(i[2], i[1]) for i in items],
        crs=CRS_WGS84,
    )


def test_nombra_el_nucleo_habitado_mas_cercano():
    inc = _incendios((40.000, -3.000))
    n = _nucleos(("Cercano", 40.02, -3.00, 500), ("Lejano", 40.50, -3.00, 90000))

    fila = contexto.anadir_distancia_poblacion(inc, n).iloc[0]

    assert fila["nucleo_cercano"] == "Cercano"
    assert 1.5 < fila["nucleo_cercano_km"] < 3.0
    assert fila["nucleo_cercano_habitantes"] == 500


def test_los_nucleos_deshabitados_no_cuentan():
    """Cero habitantes son despoblados y polígonos industriales.

    Decir que un incendio está a 800 m de un núcleo deshabitado alarma sin
    motivo, y además desplaza al pueblo que sí importa.
    """
    inc = _incendios((40.000, -3.000))
    n = _nucleos(("Despoblado", 40.005, -3.000, 0), ("Con gente", 40.05, -3.00, 800))

    fila = contexto.anadir_distancia_poblacion(inc, n).iloc[0]

    assert fila["nucleo_cercano"] == "Con gente"


def test_en_despoblado_no_se_nombra_un_pueblo_a_58_km():
    """Por encima del umbral no se nombra ninguno: ocuparía una línea de la
    ficha para no informar de nada."""
    inc = _incendios((40.0, -3.0))
    n = _nucleos(("Muy lejos", 41.0, -3.0, 5000))  # ~111 km

    fila = contexto.anadir_distancia_poblacion(inc, n).iloc[0]

    assert pd.isna(fila["nucleo_cercano"])
    assert pd.isna(fila["nucleo_cercano_km"])


def test_sin_capa_de_nucleos_los_campos_quedan_nulos():
    """El pipeline sigue sin la capa: es enriquecimiento, no un requisito."""
    salida = contexto.anadir_distancia_poblacion(
        _incendios((40.0, -3.0)),
        gpd.GeoDataFrame({"nombre": [], "habitantes": []}, geometry=[], crs=CRS_WGS84),
    )

    assert pd.isna(salida.iloc[0]["nucleo_cercano"])


def test_un_incendio_equidistante_no_se_duplica():
    inc = _incendios((40.0, -3.0))
    n = _nucleos(("Norte", 40.02, -3.0, 100), ("Sur", 39.98, -3.0, 100))

    salida = contexto.anadir_distancia_poblacion(inc, n)

    assert len(salida) == 1


# --- Qué vías están cortadas, no solo cuántas -------------------------------
#
# «2 cortes cerca» no responde a la pregunta de quien vive al lado, que es
# **cuál**. La carretera y el punto kilométrico ya venían en el esquema de
# tráfico y se descartaban en el cruce.


def _cortes_con_via(*items: tuple[float, float, bool, str, float | None]) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "por_incendio": [i[2] for i in items],
            "carretera": [i[3] for i in items],
            "pk": [i[4] for i in items],
        },
        geometry=[Point(i[1], i[0]) for i in items],
        crs=CRS_WGS84,
    )


def test_nombra_la_via_y_su_punto_kilometrico():
    inc = _incendios((40.0, -3.0))
    c = _cortes_con_via((40.02, -3.02, True, "N-630", 412.0))

    fila = contexto.anadir_cortes(inc, c).iloc[0]

    assert fila["cortes_vias"] == "N-630 pk 412.0"


def test_los_cortes_por_incendio_van_primero():
    """Un corte declarado por incendio lo ha provocado este fuego; uno de obras
    llevaba ahí desde marzo. Mezclarlos haría creer que el incendio ha cortado
    media provincia."""
    inc = _incendios((40.0, -3.0))
    c = _cortes_con_via(
        (40.01, -3.01, False, "A-1", 10.0),
        (40.02, -3.02, True, "Z-9", 20.0),
    )

    assert contexto.anadir_cortes(inc, c).iloc[0]["cortes_vias"].startswith("Z-9")


def test_a_igualdad_el_orden_es_estable():
    """Sin un criterio fijo, la lista bailaría entre ejecuciones con los mismos
    datos y cada publicación parecería un cambio."""
    inc = _incendios((40.0, -3.0))
    c = _cortes_con_via(
        (40.02, -3.02, True, "N-320", 80.0),
        (40.01, -3.01, True, "A-52", 150.0),
    )

    assert contexto.anadir_cortes(inc, c).iloc[0]["cortes_vias"] == "A-52 pk 150.0 · N-320 pk 80.0"


def test_con_muchas_vias_se_dice_cuantas_quedan_fuera():
    """Truncar en silencio haría creer que esas son todas las cortadas."""
    inc = _incendios((40.0, -3.0))
    c = _cortes_con_via(
        *[(40.0 + i / 1000, -3.0, False, f"N-{100 + i}", float(i)) for i in range(6)]
    )

    vias = contexto.anadir_cortes(inc, c).iloc[0]["cortes_vias"]

    assert vias.count("·") == contexto.MAX_VIAS
    assert "y 3 más" in vias


def test_una_via_repetida_no_se_lista_dos_veces():
    """Dos cortes en el mismo punto de la misma vía son un corte para quien
    quiere saber si puede pasar."""
    inc = _incendios((40.0, -3.0))
    c = _cortes_con_via(
        (40.01, -3.01, False, "N-630", 412.0),
        (40.02, -3.02, False, "N-630", 412.0),
    )

    assert contexto.anadir_cortes(inc, c).iloc[0]["cortes_vias"] == "N-630 pk 412.0"


def test_sin_cortes_cerca_las_vias_quedan_nulas():
    """Nulo y no cadena vacía: el frontend distingue «no hay» de «no se sabe»."""
    inc = _incendios((40.0, -3.0))
    c = _cortes_con_via((41.5, -3.0, True, "N-630", 412.0))

    fila = contexto.anadir_cortes(inc, c).iloc[0]

    assert fila["cortes_cerca"] == 0
    assert fila["cortes_vias"] is None or pd.isna(fila["cortes_vias"])


def test_un_corte_sin_carretera_no_rompe_la_lista():
    """La DGT no siempre rellena el identificador de la vía."""
    inc = _incendios((40.0, -3.0))
    c = _cortes_con_via(
        (40.01, -3.01, True, None, None),
        (40.02, -3.02, True, "N-630", 412.0),
    )

    assert contexto.anadir_cortes(inc, c).iloc[0]["cortes_vias"] == "N-630 pk 412.0"


def test_una_via_sin_punto_kilometrico_se_nombra_igual():
    inc = _incendios((40.0, -3.0))
    c = _cortes_con_via((40.01, -3.01, True, "CL-626", None))

    assert contexto.anadir_cortes(inc, c).iloc[0]["cortes_vias"] == "CL-626"
