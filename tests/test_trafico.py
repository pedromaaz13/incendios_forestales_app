"""Cortes de tráfico de la DGT.

El caso que justifica esta capa es `por_incendio`: la DGT declara la causa, y
una de sus causas es `forestFire`. Es la única fuente del proyecto que permite
afirmar una relación con el fuego sin deducirla, así que la mayoría de estas
pruebas vigilan que esa marca sea fiel al dato y que no se ponga por proximidad.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from incendios import trafico

AHORA = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)


def _registro(
    *,
    gestion: str | None = "carriagewayClosures",
    causa: str = "roadMaintenance",
    detalle_tag: str = "roadMaintenanceType",
    detalle: str | None = "roadworks",
    lat: str | None = "40.4168",
    lon: str | None = "-3.7038",
    version: str = "2026-07-28T12:00:00.000+02:00",
) -> str:
    gestion_xml = (
        f"<roadOrCarriagewayOrLaneManagementType>{gestion}"
        "</roadOrCarriagewayOrLaneManagementType>"
        if gestion
        else ""
    )
    detalle_xml = f"<{detalle_tag}>{detalle}</{detalle_tag}>" if detalle else ""
    coords = (
        f"<pointCoordinates><latitude>{lat}</latitude>"
        f"<longitude>{lon}</longitude></pointCoordinates>"
        if lat and lon
        else ""
    )
    return f"""
      <situationRecord xsi:type="RoadOrCarriagewayOrLaneManagement">
        <situationRecordVersionTime>{version}</situationRecordVersionTime>
        <validity><validityStatus>active</validityStatus>
          <validityTimeSpecification>
            <overallStartTime>2026-07-27T08:00:00.000+02:00</overallStartTime>
          </validityTimeSpecification>
        </validity>
        <cause><causeType>{causa}</causeType>
          <detailedCauseType>{detalle_xml}</detailedCauseType>
        </cause>
        <locationReference>
          <supplementaryPositionalDescription>
            <roadInformation><roadName>AV-502</roadName></roadInformation>
          </supplementaryPositionalDescription>
          <tpegLinearLocation><to>{coords}
            <_tpegNonJunctionPointExtension><extendedTpegNonJunctionPoint>
              <autonomousCommunity>Castilla y León</autonomousCommunity>
              <kilometerPoint>31.48</kilometerPoint>
              <municipality>El Tiemblo</municipality>
              <province>Ávila</province>
            </extendedTpegNonJunctionPoint></_tpegNonJunctionPointExtension>
          </to></tpegLinearLocation>
        </locationReference>
        {gestion_xml}
      </situationRecord>"""


def _publicacion(*registros: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<d2:payload xmlns:d2="http://d2" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        + "".join(registros)
        + "</d2:payload>"
    )


# --- la marca de incendio ----------------------------------------------------


def test_fire_caused_closure_is_flagged():
    """La DGT declara `forestFire`: aquí no se deduce nada."""
    df = trafico.parse(
        _publicacion(
            _registro(
                causa="environmentalObstruction",
                detalle_tag="environmentalObstructionType",
                detalle="forestFire",
            )
        ),
        AHORA,
    )

    fila = df.iloc[0]
    assert bool(fila["por_incendio"]) is True
    assert fila["detalle"] == "incendio forestal"
    assert fila["causa"] == "obstáculo natural"


@pytest.mark.parametrize("detalle", ["rockfalls", "flooding", "avalanches", "roadworks"])
def test_other_causes_are_not_flagged_as_fire(detalle):
    """Un desprendimiento o una inundación no son un incendio, por mucho que
    compartan la categoría de obstáculo natural."""
    df = trafico.parse(
        _publicacion(
            _registro(
                causa="environmentalObstruction",
                detalle_tag="environmentalObstructionType",
                detalle=detalle,
            )
        ),
        AHORA,
    )

    assert bool(df["por_incendio"].iloc[0]) is False


def test_proximity_never_sets_the_fire_flag():
    """La marca sale del vocabulario de la DGT, no de estar cerca de un foco.

    Un accidente a 2 km de un incendio es una coincidencia. Si esta prueba
    fallara sería porque alguien ha empezado a inferir causas.
    """
    df = trafico.parse(
        _publicacion(_registro(causa="accident", detalle_tag="accidentType", detalle="accident")),
        AHORA,
    )

    assert bool(df["por_incendio"].iloc[0]) is False
    assert df["causa"].iloc[0] == "accidente"


# --- filtrado ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("gestion", "esperado"),
    [
        ("roadClosed", "carretera cerrada"),
        ("carriagewayClosures", "calzada cortada"),
        ("laneClosures", "carril cortado"),
    ],
)
def test_publishes_what_actually_closes(gestion, esperado):
    df = trafico.parse(_publicacion(_registro(gestion=gestion)), AHORA)

    assert df["cierre"].iloc[0] == esperado


@pytest.mark.parametrize("gestion", ["narrowLanes", "lanesDeviated", "newRoadworksLayout"])
def test_discards_what_still_lets_you_through(gestion):
    """Carriles estrechados o desviados dejan pasar. Publicarlos sumaría cientos
    de puntos que enterrarían los cortes reales."""
    assert trafico.parse(_publicacion(_registro(gestion=gestion)), AHORA).empty


def test_fire_closures_survive_without_a_declared_closure_type():
    """Cuatro de los 34 cortes por incendio no traen grado de corte. Filtrarlos
    perdería justo los que más importan."""
    df = trafico.parse(
        _publicacion(
            _registro(
                gestion=None,
                causa="environmentalObstruction",
                detalle_tag="environmentalObstructionType",
                detalle="forestFire",
            )
        ),
        AHORA,
    )

    assert len(df) == 1
    assert bool(df["por_incendio"].iloc[0]) is True
    assert df["cierre"].iloc[0] is None


def test_records_without_coordinates_are_dropped():
    """Un corte sin sitio no informa de nada."""
    assert trafico.parse(_publicacion(_registro(lat=None, lon=None)), AHORA).empty


# --- localización ------------------------------------------------------------


def test_extracts_the_full_location():
    """Provincia, municipio y punto kilométrico vienen anidados en la extensión
    del perfil español, no en el DATEX II genérico."""
    df = trafico.parse(_publicacion(_registro()), AHORA)

    fila = df.iloc[0]
    assert fila["carretera"] == "AV-502"
    assert fila["municipio"] == "El Tiemblo"
    assert fila["provincia"] == "Ávila"
    assert fila["comunidad"] == "Castilla y León"
    assert fila["pk"] == "31.48"
    assert fila["latitude"] == pytest.approx(40.4168)


def test_age_travels_with_each_closure():
    """El feed marca todo como `active`, incluidos registros de hace meses. Sin
    la antigüedad no hay forma de juzgar si un corte sigue vigente."""
    df = trafico.parse(
        _publicacion(_registro(version="2026-07-27T12:00:00.000+00:00")), AHORA
    )

    assert df["edad_dias"].iloc[0] == pytest.approx(2.0, abs=0.1)


def test_unparseable_date_is_none_not_zero():
    """Una edad de cero diría "recién actualizado" justo cuando no se sabe."""
    df = trafico.parse(_publicacion(_registro(version="fecha rara")), AHORA)

    assert df["edad_dias"].iloc[0] is None


# --- robustez ----------------------------------------------------------------


def test_malformed_xml_returns_empty_with_schema(caplog):
    with caplog.at_level("ERROR"):
        df = trafico.parse("<esto no es xml", AHORA)

    assert df.empty
    assert list(df.columns) == trafico.TRAFICO_SCHEMA
    assert any("ilegible" in r.getMessage() for r in caplog.records)


def test_empty_publication_keeps_the_schema():
    df = trafico.parse(_publicacion(), AHORA)

    assert df.empty
    assert list(df.columns) == trafico.TRAFICO_SCHEMA


def test_fetch_failure_returns_empty_without_raising(caplog):
    """Los cortes son contexto: sin ellos el mapa de incendios sigue sirviendo."""
    with (
        caplog.at_level("ERROR"),
        httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(503, text="nope"))
        ) as client,
    ):
        gdf = trafico.fetch(client)

    assert gdf.empty
    assert any("no disponible" in r.getMessage() for r in caplog.records)


def test_fetch_identifies_itself():
    """User-Agent con contacto: si a la DGT le extraña el tráfico, que pueda ver
    quién es antes de bloquearlo."""
    capturado = {}

    def handler(request):
        capturado["ua"] = request.headers.get("user-agent", "")
        return httpx.Response(200, text=_publicacion(_registro()))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        trafico.fetch(client)

    assert "incendios-es" in capturado["ua"]
    assert "github.com" in capturado["ua"]


def test_to_gdf_produces_valid_geometry():
    gdf = trafico.to_gdf(trafico.parse(_publicacion(_registro()), AHORA))

    assert gdf.crs.to_epsg() == 4326
    assert gdf.geometry.is_valid.all()


def test_national_feed_is_used_not_the_catalan_one():
    """El feed de `infocar.dgt.es/datex2/sct/` solo trae Cataluña: 92 cortes en
    4 provincias. Publicarlo como "cortes de la DGT" haría que alguien en Ávila
    viera cero y concluyese que no hay ninguno."""
    assert "nap.dgt.es" in trafico.DATEX_URL
    assert "/sct/" not in trafico.DATEX_URL
