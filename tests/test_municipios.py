"""Puerta de validación de la capa municipal · RF-P-07.

Lo que se prueba aquí no es que la capa buena se acepte —eso es lo fácil— sino
que **las malas se rechacen**. Nombrar mal un incendio es peor que no nombrarlo:
alguien busca su pueblo en la lista, no lo ve, y se queda tranquilo. Una capa
recortada, una descarga que devolvió HTML o un servicio que cambió de esquema
producirían exactamente eso, y en silencio.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
muni = importlib.import_module("preparar_municipios")


def _capa(n: int, *, columna="NAMEUNIT", bbox=(-9.3, 36.0, 3.3, 43.8), **extra):
    """Rejilla de recintos que cubre el bbox indicado.

    No son municipios reales: son celdas. Para la puerta de validación da igual
    —comprueba número, esquema y cobertura— salvo en el muestreo, que necesita
    que los nombres de control caigan donde toca y se inyecta con `extra`.
    """
    oeste, sur, este, norte = bbox
    lado = ((este - oeste) * (norte - sur) / n) ** 0.5

    celdas, nombres = [], []
    y = sur
    while y < norte and len(celdas) < n:
        x = oeste
        while x < este and len(celdas) < n:
            celdas.append(box(x, y, x + lado, y + lado))
            nombres.append(f"Celda {len(celdas)}")
            x += lado
        y += lado

    datos = {columna: nombres, "geometry": celdas}
    datos.update({k: [v] * len(celdas) for k, v in extra.items()})
    return gpd.GeoDataFrame(datos, crs=4326)


def _capa_con_controles(n: int = 8000):
    """Capa que sí satisface el muestreo: un recinto por municipio de control."""
    gdf = _capa(n - len(muni.CONTROL))
    recintos, nombres = [], []
    for lat, lon, esperado in muni.CONTROL:
        recintos.append(box(lon - 0.05, lat - 0.05, lon + 0.05, lat + 0.05))
        nombres.append(esperado.capitalize())

    controles = gpd.GeoDataFrame({"NAMEUNIT": nombres, "geometry": recintos}, crs=4326)
    # Los controles van primero para que `sjoin` los encuentre antes que la celda.
    return gpd.GeoDataFrame(
        __import__("pandas").concat([controles, gdf], ignore_index=True), crs=4326
    )


# --- la capa buena pasa ------------------------------------------------------


def test_valid_layer_is_accepted():
    columna, _ = muni.validar(_capa_con_controles())

    assert columna == "NAMEUNIT"


def test_province_column_is_detected():
    gdf = _capa_con_controles()
    gdf["provincia"] = "Provincia"

    _, provincia = muni.validar(gdf)

    assert provincia == "provincia"


# --- 1 · número de recintos --------------------------------------------------


def test_rejects_a_layer_cut_to_one_region():
    """~200 recintos es una comunidad suelta, no España."""
    with pytest.raises(muni.CapaNoValida, match="fuera del rango"):
        muni.validar(_capa(200))


def test_rejects_provinces_instead_of_municipalities():
    """52 provincias pasarían el bbox y el esquema, pero no son municipios."""
    with pytest.raises(muni.CapaNoValida, match="fuera del rango"):
        muni.validar(_capa(52))


def test_rejects_an_absurdly_large_layer():
    with pytest.raises(muni.CapaNoValida, match="fuera del rango"):
        muni.validar(_capa(20_000))


# --- 2 · esquema -------------------------------------------------------------


def test_rejects_a_layer_without_a_name_column():
    """Sin nombre no hay geocoding: la capa no sirve para nada."""
    gdf = _capa_con_controles().rename(columns={"NAMEUNIT": "COLUMNA_RARA"})

    with pytest.raises(muni.CapaNoValida, match="nombre reconocible"):
        muni.validar(gdf)


def test_error_lists_the_columns_it_did_receive():
    """El mensaje tiene que permitir arreglarlo sin abrir el fichero a mano."""
    gdf = _capa_con_controles().rename(columns={"NAMEUNIT": "MUNI_V2"})

    with pytest.raises(muni.CapaNoValida) as exc:
        muni.validar(gdf)

    assert "MUNI_V2" in str(exc.value)


# --- 3 · cobertura -----------------------------------------------------------


def test_rejects_a_layer_from_another_country():
    """Francia tiene ~35.000 comunas; recortado a 8.000 pasaría el recuento."""
    with pytest.raises(muni.CapaNoValida, match="se sale de España|recortada"):
        muni.validar(_capa(8000, bbox=(-5.0, 42.5, 8.0, 51.0)))


def test_rejects_a_layer_covering_only_a_narrow_strip():
    with pytest.raises(muni.CapaNoValida, match="recortada"):
        muni.validar(_capa(8000, bbox=(-2.0, 39.0, 1.0, 42.0)))


# --- 4 · muestreo contra municipios conocidos --------------------------------


def test_rejects_a_layer_whose_geometry_does_not_match_reality():
    """El caso más peligroso: recuento, esquema y bbox correctos, pero los
    polígonos asignan municipios equivocados. Solo el muestreo lo detecta."""
    with pytest.raises(muni.CapaNoValida, match="muestreo"):
        muni.validar(_capa(8000))  # celdas anónimas, ningún control encaja


def test_tolerates_a_single_control_mismatch():
    """Algunos cascos urbanos caen en enclaves del municipio vecino: un fallo
    aislado no invalida la capa."""
    gdf = _capa_con_controles()
    gdf.loc[0, "NAMEUNIT"] = "Otro municipio"

    columna, _ = muni.validar(gdf)

    assert columna == "NAMEUNIT"


# --- escritura ---------------------------------------------------------------


def test_nothing_is_written_when_validation_fails(tmp_path):
    """La garantía que hace segura toda la operación: una capa mala no puede
    sobrescribir una buena."""
    destino = tmp_path / "municipios.geojson"
    origen = tmp_path / "mala.geojson"
    _capa(200).to_file(origen, driver="GeoJSON")

    with pytest.raises(muni.CapaNoValida):
        muni.preparar(origen, destino)

    assert not destino.exists()


def test_writes_and_simplifies_a_valid_layer(tmp_path):
    destino = tmp_path / "municipios.geojson"
    origen = tmp_path / "buena.geojson"
    _capa_con_controles().to_file(origen, driver="GeoJSON")

    resumen = muni.preparar(origen, destino)

    assert destino.exists()
    assert resumen["municipios"] >= muni.MIN_MUNICIPIOS
    escrita = gpd.read_file(destino)
    # El nombre se normaliza a lo que `enrich.py` busca primero.
    assert "NAMEUNIT" in escrita.columns
    assert escrita.crs.to_epsg() == 4326


def test_output_feeds_enrich(tmp_path):
    """La prueba que de verdad importa: la capa preparada tiene que servirle a
    `enrich.py` tal cual, sin adaptadores por medio."""
    from incendios import enrich

    from conftest import make_fires

    destino = tmp_path / "municipios.geojson"
    origen = tmp_path / "buena.geojson"
    _capa_con_controles().to_file(origen, driver="GeoJSON")
    muni.preparar(origen, destino)

    # Un incendio sobre Madrid.
    fires = make_fires([{"fire_id": "f1", "latitude": 40.4168, "longitude": -3.7038}])
    out = enrich.enrich_admin(fires, path=destino)

    assert out["municipio"].iloc[0].lower().startswith("madrid")


def test_layer_without_crs_is_rejected(tmp_path):
    """Sin CRS declarado no se puede reproyectar con seguridad, y asumir 4326
    sobre una capa en UTM colocaría España en el golfo de Guinea."""
    destino = tmp_path / "municipios.geojson"
    origen = tmp_path / "sin_crs.geojson"
    gdf = _capa_con_controles()
    gdf.to_file(origen, driver="GeoJSON")

    import geopandas

    original = geopandas.read_file
    try:
        geopandas.read_file = lambda *a, **k: gdf.set_crs(None, allow_override=True)
        muni.gpd.read_file = geopandas.read_file
        with pytest.raises(muni.CapaNoValida, match="CRS"):
            muni.preparar(origen, destino)
    finally:
        geopandas.read_file = original
        muni.gpd.read_file = original


# --- selección de capa dentro de una descarga --------------------------------


def test_rejects_a_line_layer_with_a_useful_message(tmp_path):
    """La serie del IGN se llama "líneas límite" y son literalmente líneas.

    Con líneas no hay point-in-polygon. Rechazarlo con un mensaje que diga qué
    buscar ahorra media hora de desconcierto a quien acaba de descargarlo.
    """
    from shapely.geometry import LineString

    origen = tmp_path / "lineas_limite.geojson"
    gpd.GeoDataFrame(
        {"NAMEUNIT": ["l1", "l2"], "geometry": [LineString([(0, 40), (1, 41)])] * 2},
        crs=4326,
    ).to_file(origen, driver="GeoJSON")

    with pytest.raises(muni.CapaNoValida, match="recintos"):
        muni.preparar(origen, tmp_path / "salida.geojson")


def test_prefers_polygon_layers_over_line_layers_in_a_folder(tmp_path):
    """En la descarga vienen las dos capas juntas: hay que probar la buena
    primero, no fallar en la primera por orden alfabético."""
    carpeta = tmp_path / "descarga"
    carpeta.mkdir()
    (carpeta / "aa_lineas_limite.geojson").write_text("{}", encoding="utf-8")
    (carpeta / "zz_recintos_municipales.geojson").write_text("{}", encoding="utf-8")

    lotes = muni.candidatos_en(carpeta)

    assert lotes[0][0].name == "zz_recintos_municipales.geojson"


def test_picks_administrative_unit_4th_order_from_the_ign_download(tmp_path):
    """El README del IGN describe exactamente qué hay dentro:

        AdministrativeBoundary → líneas (fronteras, autonómicas, provinciales,
                                 municipales)
        AdministrativeUnit     → superficies (país, comunidades, provincias,
                                 municipios)

    De las ocho combinaciones solo sirve AdministrativeUnit + 4thOrder.
    """
    carpeta = tmp_path / "lineas_limite_gml"
    carpeta.mkdir()
    for nombre in (
        "AdministrativeBoundary_1stOrder.gml",
        "AdministrativeBoundary_4thOrder.gml",
        "AdministrativeUnit_1stOrder.gml",
        "AdministrativeUnit_2ndOrder.gml",
        "AdministrativeUnit_3rdOrder.gml",
        "AdministrativeUnit_4thOrder.gml",
    ):
        (carpeta / nombre).write_text("<gml/>", encoding="utf-8")

    lotes = muni.candidatos_en(carpeta)

    assert [f.name for f in lotes[0]] == ["AdministrativeUnit_4thOrder.gml"]
    # Y las líneas quedan las últimas, no se prueban antes por casualidad.
    assert all("Boundary" in f.name for f in lotes[-1])


def test_groups_a_layer_split_across_several_files(tmp_path):
    """El IGN parte las capas: "cada archivo .gml contiene como máximo 10000
    entidades". Probados de uno en uno, los ~8.130 municipios repartidos en
    dos ficheros fallarían el recuento en los dos. Van juntos."""
    carpeta = tmp_path / "descarga"
    carpeta.mkdir()
    for i in (1, 2, 3):
        (carpeta / f"AdministrativeUnit_4thOrder_{i}.gml").write_text("<gml/>", encoding="utf-8")

    lotes = muni.candidatos_en(carpeta)

    assert len(lotes[0]) == 3


def test_concatenates_a_split_layer_before_validating(tmp_path):
    """La prueba de que la concatenación es lo que salva el caso: dos mitades
    que por separado no llegan al mínimo, juntas sí."""
    mitad_a = tmp_path / "AdministrativeUnit_4thOrder_1.geojson"
    mitad_b = tmp_path / "AdministrativeUnit_4thOrder_2.geojson"

    completa = _capa_con_controles(8000)
    completa.iloc[:4000].to_file(mitad_a, driver="GeoJSON")
    completa.iloc[4000:].to_file(mitad_b, driver="GeoJSON")

    # Por separado, cada mitad falla el recuento.
    with pytest.raises(muni.CapaNoValida, match="fuera del rango"):
        muni.preparar(mitad_a, tmp_path / "no.geojson")

    # Juntas, pasan.
    destino = tmp_path / "municipios.geojson"
    resumen = muni.preparar([mitad_a, mitad_b], destino)

    assert resumen["municipios"] == 8000
    assert destino.exists()


def test_batches_with_mixed_crs_are_reprojected(tmp_path):
    """Canarias viene en REGCAN95 y la península en ETRS89. Concatenar sin
    reproyectar mezclaría coordenadas de sistemas distintos."""
    a = tmp_path / "AdministrativeUnit_4thOrder_a.geojson"
    b = tmp_path / "AdministrativeUnit_4thOrder_b.geojson"

    completa = _capa_con_controles(8000)
    completa.iloc[:4000].to_file(a, driver="GeoJSON")
    completa.iloc[4000:].to_crs(25830).to_file(b, driver="GeoJSON")

    unido = muni.leer_lote([a, b])

    assert len(unido) == 8000
    assert unido.crs.to_epsg() == 4326


def test_finds_layers_inside_a_zip(tmp_path):
    import zipfile

    origen = tmp_path / "descarga.zip"
    with zipfile.ZipFile(origen, "w") as z:
        z.writestr("subcarpeta/recintos_municipales.geojson", "{}")
        z.writestr("subcarpeta/leeme.txt", "no es una capa")

    lotes = muni.candidatos_en(origen)

    assert len(lotes) == 1
    assert lotes[0][0].name == "recintos_municipales.geojson"


def test_polygon_detection():
    from shapely.geometry import LineString, Polygon

    poligonos = gpd.GeoDataFrame(geometry=[Polygon([(0, 0), (1, 0), (1, 1)])], crs=4326)
    lineas = gpd.GeoDataFrame(geometry=[LineString([(0, 0), (1, 1)])], crs=4326)

    assert muni.es_poligonal(poligonos)
    assert not muni.es_poligonal(lineas)


def test_empty_folder_says_what_it_looked_for(tmp_path):
    vacia = tmp_path / "vacia"
    vacia.mkdir()
    (vacia / "leeme.txt").write_text("nada", encoding="utf-8")

    with pytest.raises(muni.CapaNoValida, match="reconocible"):
        muni.candidatos_en(vacia)
