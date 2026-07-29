"""Genera un juego de datos de demostración en `web/public/live/`.

Para qué: el frontend necesita datos para poder mirarse, y los reales exigen
`FIRMS_MAP_KEY` y salida a internet. Esto produce artefactos con el contrato
exacto de las secciones 4.1-4.3 a partir de datos sintéticos, de modo que
`npm run dev` funcione en un portátil recién clonado.

**No son datos reales y el manifiesto lo dice.** `demo: true` viaja en
`manifest.json` y el frontend pinta una banda permanente con ese aviso. Un juego
de demostración indistinguible de la producción sería justo el tipo de engaño
que este proyecto existe para evitar.

Uso:  PYTHONPATH=src python scripts/build_demo_data.py
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from incendios import aire as aire_mod
from incendios import build as build_mod
from incendios import clean as clean_mod
from incendios import cluster as cluster_mod
from incendios import merge as merge_mod
from incendios import trafico as trafico_mod
from incendios import validate as validate_mod
from incendios import wind as wind_mod
from incendios.health import HealthReport, SourceHealth
from incendios.pipeline import setup_logging

DESTINO = Path(__file__).resolve().parents[1] / "web" / "public" / "live"

rng = np.random.default_rng(20260727)
# El instante es "ahora" para que la demo se vea viva al generarla. Las capturas
# de regresión de la sección 9 usan sus propios fixtures congelados.
NOW = pd.Timestamp.now(tz="UTC").floor("min")

# Focos repartidos por las comunidades con más actividad en julio. Las
# coordenadas son de zonas forestales reales para que el mapa resulte creíble,
# pero los incendios son inventados.
FOCOS = [
    # (nombre, lat, lon, n_hotspots, dispersión, FRP medio, horas)
    ("Sierra de Gata", 40.250, -6.605, 130, 0.012, 30.0, [2, 3, 14, 15]),
    ("Las Hurdes", 40.430, -6.180, 42, 0.008, 22.0, [3, 4]),
    ("Valle del Tiétar", 40.230, -4.980, 18, 0.006, 14.0, [5]),
    ("Sierra de Ancares", 42.830, -6.870, 26, 0.007, 18.0, [6, 7]),
    ("Ribeira Sacra", 42.400, -7.850, 9, 0.004, 9.0, [4]),
    ("Montsec", 42.050, 0.850, 14, 0.005, 12.0, [8]),
    ("Serra de Espadà", 39.900, -0.380, 22, 0.006, 16.0, [2, 3]),
    ("Sierra de Segura", 38.280, -2.640, 31, 0.008, 24.0, [9, 10]),
    ("Sierra de Aracena", 37.900, -6.560, 12, 0.005, 11.0, [7]),
    ("Cabañeros", 39.400, -4.480, 7, 0.004, 8.0, [11]),
    ("Serra de Tramuntana", 39.750, 2.760, 5, 0.003, 7.0, [6]),
    ("Cumbre Vieja", 28.570, -17.840, 11, 0.005, 26.0, [3], "canarias"),
    # Antorcha industrial: la máscara debe suprimirla y el contador registrarlo.
    ("Refinería Puertollano", 38.703, -4.092, 8, 0.001, 25.0, [1, 13]),
    # Baja confianza: cae en el filtro.
    ("Quema agrícola La Mancha", 39.100, -2.400, 15, 0.010, 3.0, [5]),
]

# Partes oficiales. Los `source_id` son los de RF-P-03; los datos son inventados.
OFICIALES = [
    # cerca de Sierra de Gata -> debe emparejar con el cluster
    dict(source_id="jcyl", external_id="CYL-2026-0412", latitude=40.252, longitude=-6.598,
         precision_m=500.0, status="activo", municipio="Descargamaría", provincia="Cáceres",
         level=2, resources="16 aéreos · 80 terrestres · 54 personas", horas=6),
    dict(source_id="jcyl", external_id="CYL-2026-0418", latitude=42.828, longitude=-6.874,
         precision_m=500.0, status="estabilizado", municipio="Candín", provincia="León",
         level=1, resources="4 aéreos · 22 terrestres", horas=9),
    dict(source_id="bombers", external_id="CAT-88231", latitude=42.048, longitude=0.856,
         precision_m=1500.0, status="controlado", municipio="Àger", provincia="Lleida",
         level=1, resources="12 dotacions", horas=10),
    dict(source_id="infoca", external_id="AND-2026-1177", latitude=38.284, longitude=-2.633,
         precision_m=1500.0, status="activo", municipio="Segura de la Sierra", provincia="Jaén",
         level=1, resources="6 medios aéreos · 45 personas", horas=11),
    # INFOCAM: centroide municipal, +-6 km. Es el caso del anillo grande.
    dict(source_id="infocam", external_id="CLM-FID-5521", latitude=39.455, longitude=-4.520,
         precision_m=6000.0, status="activo", municipio="Retuerta del Bullaque",
         provincia="Ciudad Real", level=1, resources="3 medios", horas=12),
    # 112 CV: coordenada precisa
    dict(source_id="112cv", external_id="CV-2026-33917", latitude=39.902, longitude=-0.377,
         precision_m=100.0, status="activo", municipio="Eslida", provincia="Castelló",
         level=1, resources="2 aéreos · 18 terrestres", horas=3),
    # Huérfano oficial: sin cluster satelital cerca. Debe seguir apareciendo.
    dict(source_id="112cv", external_id="CV-2026-33920", latitude=38.760, longitude=-0.640,
         precision_m=100.0, status="activo", municipio="Alcoi", provincia="Alacant",
         level=1, resources="1 aéreo · 8 terrestres", horas=1),
    # Huérfano de INFOCAM: es el caso que enseña el anillo de +-6 km.
    #
    # Cuando un parte SÍ empareja con un cluster FIRMS, la posición que se
    # publica es la del cluster (375 m), porque es la más precisa de las dos, y
    # el anillo sale pequeño con razón. El margen de 6 km solo se dibuja cuando
    # la única posición disponible es el centroide municipal, que es
    # precisamente cuando hace falta avisar de él.
    dict(source_id="infocam", external_id="CLM-FID-5530", latitude=39.870, longitude=-2.180,
         precision_m=6000.0, status="activo", municipio="Villalba de la Sierra",
         provincia="Cuenca", level=1, resources="2 medios terrestres", horas=2),
]

FUENTES_META = {
    "jcyl": ("Junta de Castilla y León", "Castilla y León", 500.0),
    "bombers": ("Bombers de la Generalitat", "Cataluña", 1500.0),
    "infoca": ("Plan INFOCA", "Andalucía", 1500.0),
    "infocam": ("INFOCAM / FIDIAS", "Castilla-La Mancha", 6000.0),
    "112cv": ("112 Comunitat Valenciana", "Comunitat Valenciana", 100.0),
}


def bloque(nombre, lat, lon, n, spread, frp, horas, area="peninsula", **extra):
    return pd.DataFrame(
        {
            "latitude": lat + rng.normal(0, spread, n),
            "longitude": lon + rng.normal(0, spread, n),
            "acq_dt": [NOW - pd.Timedelta(hours=float(h)) for h in rng.choice(horas, n)],
            "satellite": "N",
            "instrument": extra.get("instrument", "VIIRS"),
            "source": extra.get("source", "VIIRS_NOAA20_NRT"),
            "area_key": area,
            "confidence_raw": extra.get("confidence_raw", "n"),
            "confidence_pct": extra.get("confidence_pct", 60.0),
            "brightness_k": rng.normal(335, 12, n),
            "frp_mw": rng.gamma(2.0, frp / 2.0, n),
            "daynight": "D",
            "scan": 0.4,
            "track": 0.4,
        }
    )


def construir_hotspots() -> pd.DataFrame:
    bloques = []
    for foco in FOCOS:
        nombre, lat, lon, n, spread, frp, horas = foco[:7]
        area = foco[7] if len(foco) > 7 else "peninsula"
        extra = {}
        if "Quema agrícola" in nombre:
            extra = {"confidence_raw": "l", "confidence_pct": 20.0}
        bloques.append(bloque(nombre, lat, lon, n, spread, frp, horas, area, **extra))

    # Un puñado de detecciones SEVIRI para que el selector de sensor tenga algo
    # que filtrar y la latencia geoestacionaria se vea en el manifiesto.
    bloques.append(
        bloque("SEVIRI Sierra de Gata", 40.251, -6.601, 4, 0.02, 40.0, [0.25],
               instrument="SEVIRI", source="SEVIRI_FRP_PIXEL")
    )
    return pd.concat(bloques, ignore_index=True)


def construir_oficiales() -> pd.DataFrame:
    filas = []
    for o in OFICIALES:
        fila = dict(o)
        horas = fila.pop("horas")
        fila["reported_at"] = NOW - pd.Timedelta(hours=horas)
        fila["raw_status"] = fila["status"].capitalize()
        filas.append(fila)
    return pd.DataFrame(filas)


def construir_viento() -> gpd.GeoDataFrame:
    filas = []
    for nombre, lat, lon in wind_mod.GRID_POINTS:
        viene_de = float(rng.uniform(0, 360))
        velocidad = float(np.clip(rng.gamma(2.4, 7.0), 2, 78))
        filas.append(
            {
                "name": nombre,
                "latitude": lat,
                "longitude": lon,
                "speed_kmh": round(velocidad, 1),
                "gusts_kmh": round(velocidad * float(rng.uniform(1.3, 1.9)), 1),
                "direction_from_deg": round(viene_de, 1),
                "direction_to_deg": round(wind_mod.to_direction_deg(viene_de), 1),
                "cardinal_from": wind_mod.cardinal(viene_de),
                "observed_at": NOW.strftime("%Y-%m-%dT%H:%M"),
            }
        )
    return wind_mod.to_gdf(pd.DataFrame(filas)[wind_mod.WIND_SCHEMA])


def construir_aire() -> gpd.GeoDataFrame:
    """Calidad del aire sintética sobre la misma rejilla que el viento.

    Se genera aquí porque el E2E comprueba que el conmutador monta su capa, y sin
    el fichero la prueba fallaba con un 404 que parecía un fallo del frontend. Los
    valores salen de una gamma recortada a 0-140: reparte el peso en los tramos
    bajos, que es como se comporta el índice real, y llega a "muy mala" lo justo
    para que la leyenda tenga algo que enseñar.
    """
    filas = []
    for nombre, lat, lon in wind_mod.GRID_POINTS:
        aqi = float(np.clip(rng.gamma(2.2, 12.0), 3, 140))
        filas.append(
            {
                "name": nombre,
                "latitude": lat,
                "longitude": lon,
                "aqi": round(aqi, 1),
                "nivel": aire_mod.nivel(aqi),
                "pm2_5": round(float(np.clip(rng.gamma(2.0, 6.0), 1, 90)), 1),
                "pm10": round(float(np.clip(rng.gamma(2.4, 9.0), 2, 160)), 1),
                "co": round(float(np.clip(rng.gamma(2.0, 120.0), 40, 2400)), 1),
                "observed_at": NOW.strftime("%Y-%m-%dT%H:%M"),
            }
        )
    return aire_mod.to_gdf(pd.DataFrame(filas)[aire_mod.AIRE_SCHEMA])


def construir_trafico(official: pd.DataFrame) -> gpd.GeoDataFrame:
    """Cortes de carretera sintéticos.

    Los `por_incendio` se colocan **junto a incendios reales del juego de datos**
    en lugar de al azar. No es cosmética: la capa distingue el corte declarado por
    la DGT como causado por fuego del resto, y con puntos dispersos esa distinción
    no se podría comprobar mirando el mapa de la demo.

    Aun así el `por_incendio` sigue siendo un dato declarado, no deducido de la
    proximidad, igual que en producción.
    """
    filas = []

    for i, (_, inc) in enumerate(official.head(4).iterrows()):
        filas.append(
            {
                "id": f"demo-fuego-{i}",
                "causa": "incendio forestal",
                "detalle": "Corte por incendio forestal en las inmediaciones",
                "cierre": "carretera cerrada",
                "por_incendio": True,
                "carretera": f"N-{320 + i}",
                "pk": round(float(rng.uniform(5, 180)), 1),
                "municipio": inc.get("municipio"),
                "provincia": inc.get("provincia"),
                "comunidad": None,
                "latitude": float(inc["latitude"]) + float(rng.normal(0, 0.03)),
                "longitude": float(inc["longitude"]) + float(rng.normal(0, 0.03)),
                "desde": (NOW - pd.Timedelta(hours=float(rng.uniform(1, 20)))).isoformat(),
                "actualizado": NOW.isoformat(),
                "edad_dias": 0,
            }
        )

    otras = ("obras", "accidente", "meteorología adversa", "manifestación")
    for i in range(24):
        horas = float(rng.uniform(1, 60))
        filas.append(
            {
                "id": f"demo-otro-{i}",
                "causa": str(rng.choice(otras)),
                "detalle": "Circulación interrumpida",
                "cierre": str(rng.choice(list(trafico_mod.CIERRES.values()))),
                "por_incendio": False,
                "carretera": f"A-{int(rng.integers(1, 92))}",
                "pk": round(float(rng.uniform(1, 400)), 1),
                "municipio": None,
                "provincia": None,
                "comunidad": None,
                "latitude": round(float(rng.uniform(36.2, 43.6)), 4),
                "longitude": round(float(rng.uniform(-8.8, 3.2)), 4),
                "desde": (NOW - pd.Timedelta(hours=horas)).isoformat(),
                "actualizado": NOW.isoformat(),
                "edad_dias": int(horas // 24),
            }
        )

    return trafico_mod.to_gdf(pd.DataFrame(filas)[trafico_mod.TRAFICO_SCHEMA])


def construir_salud(official: pd.DataFrame) -> HealthReport:
    informe = HealthReport()
    informe.add(SourceHealth(
        id="firms_viirs", name="NASA FIRMS · VIIRS", region="España", kind="satelite",
        critical=True, ttl_seconds=600, precision_m=375.0,
        last_success_at=(NOW - pd.Timedelta(minutes=4)).to_pydatetime(), records=280,
        attribution="NASA FIRMS",
    ))
    informe.add(SourceHealth(
        id="firms_modis", name="NASA FIRMS · MODIS", region="España", kind="satelite",
        critical=False, ttl_seconds=600, precision_m=1000.0,
        last_success_at=(NOW - pd.Timedelta(minutes=4)).to_pydatetime(), records=34,
        attribution="NASA FIRMS",
    ))
    informe.add(SourceHealth(
        id="seviri", name="EUMETSAT LSA-SAF · SEVIRI", region="España", kind="satelite",
        critical=False, ttl_seconds=900, precision_m=3000.0,
        last_success_at=(NOW - pd.Timedelta(minutes=15)).to_pydatetime(), records=4,
        attribution="EUMETSAT LSA-SAF",
    ))

    for source_id, (nombre, region, precision) in FUENTES_META.items():
        registros = int((official["source_id"] == source_id).sum())
        # INFOCAM entra en error a propósito: el panel de estado tiene que
        # enseñar el caso interesante, no ocho filas verdes.
        caida = source_id == "infocam"
        informe.add(SourceHealth(
            id=source_id, name=nombre, region=region, kind="oficial",
            critical=False, ttl_seconds=300 if source_id != "infocam" else 600,
            precision_m=precision,
            last_success_at=None if caida else (NOW - pd.Timedelta(minutes=5)).to_pydatetime(),
            records=0 if caida else registros,
            error="HTTP 503 en el portal de origen" if caida else None,
            consecutive_failures=3 if caida else 0,
            attribution=nombre,
        ))

    informe.add(SourceHealth(
        id="open_meteo", name="Open-Meteo · viento", region="España", kind="contexto",
        critical=False, ttl_seconds=900,
        last_success_at=(NOW - pd.Timedelta(minutes=6)).to_pydatetime(),
        records=len(wind_mod.GRID_POINTS), attribution="Open-Meteo · CC BY 4.0",
    ))
    return informe


def main() -> None:
    setup_logging(verbose=False)
    DESTINO.mkdir(parents=True, exist_ok=True)

    crudos = construir_hotspots()
    hotspots = clean_mod.clean(crudos)
    suprimidos_industrial = int(
        ((crudos["latitude"].sub(38.703).abs() < 0.02)
         & (crudos["longitude"].sub(-4.092).abs() < 0.02)).sum()
    )
    suprimidos_baja = int((crudos["confidence_pct"] < 30).sum())

    hotspots = cluster_mod.assign_fire_ids(hotspots)
    fires = cluster_mod.build_fires(hotspots, now=NOW)
    perimeters = cluster_mod.build_perimeters(hotspots)

    official = construir_oficiales()
    official, fires = merge_mod.match(official, fires)
    incidents = merge_mod.build_incidents(official, fires)

    # Los municipios los pondría enrich.py con la capa del IGN, que no está en
    # el repo. En demo se rellenan desde el parte oficial para que la ficha de
    # RF-F-10 tenga contenido.
    nombres = official.set_index("fire_id")["municipio"].dropna().to_dict()
    provincias = official.set_index("fire_id")["provincia"].dropna().to_dict()
    incidents["municipio"] = incidents["municipio"].fillna(
        incidents["id"].map(nombres)
    )
    incidents["provincia"] = incidents["provincia"].fillna(
        incidents["id"].map(provincias)
    )

    # Medios e IGR desde el parte, para la ficha.
    por_id = official.dropna(subset=["fire_id"]).set_index("fire_id")
    huerfanos = official[official["fire_id"].isna()].copy()
    huerfanos.index = "off_" + huerfanos["source_id"] + "_" + huerfanos["external_id"].astype(str)
    todos = pd.concat([por_id, huerfanos])
    incidents["igr_level"] = incidents["id"].map(todos["level"].to_dict())
    incidents["resources_text"] = incidents["id"].map(todos["resources"].to_dict())

    validate_mod.validate_or_abort(incidents)

    web = build_mod.incidents_for_web(incidents)
    web["resources_text"] = incidents["resources_text"].values

    manifest = build_mod.build_manifest(
        hotspots, incidents,
        official=official,
        suppressed_industrial=suprimidos_industrial,
        suppressed_lowconf=suprimidos_baja,
        pipeline_started_at=(NOW - pd.Timedelta(minutes=4)).to_pydatetime(),
        now=NOW.to_pydatetime(),
    )
    # Marca inequívoca: estos datos no son reales y el frontend lo dirá.
    manifest["demo"] = True
    manifest["demo_reason"] = (
        "Datos de demostración generados por scripts/build_demo_data.py. "
        "No corresponden a incendios reales."
    )

    informe = construir_salud(official)
    degradado, motivo = informe.degraded(
        NOW.to_pydatetime(), manifest["worst_data_age_seconds"]
    )
    manifest["degraded"] = degradado or manifest["degraded"]
    manifest["degraded_reason"] = motivo or manifest["degraded_reason"]

    hotspots_web = hotspots[
        ["acq_dt", "frp_mw", "confidence_pct", "fire_id", "instrument", "daynight", "geometry"]
    ].copy()
    hotspots_web["acq_dt"] = hotspots_web["acq_dt"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    perimeters = perimeters.copy()
    perimeters["is_estimate"] = True  # RF-P-08: nunca sin la marca

    _escribe(web, DESTINO / "incidents.geojson")
    _escribe(hotspots_web, DESTINO / "hotspots.geojson")
    _escribe(perimeters, DESTINO / "perimeters.geojson")
    _escribe(construir_viento(), DESTINO / "wind.geojson")
    _escribe(construir_aire(), DESTINO / "aire.geojson")
    _escribe(construir_trafico(official), DESTINO / "trafico.geojson")
    informe.write(DESTINO / "sources.json", now=NOW.to_pydatetime())
    build_mod.write_manifest(manifest, DESTINO / "manifest.json")

    _reflejar_en_dist()

    print(f"\nDemo escrita en {DESTINO}")
    print(f"  incidentes : {len(web)}")
    print(f"  hotspots   : {len(hotspots_web)}")
    print(f"  suprimidos : {suprimidos_industrial} industriales · {suprimidos_baja} baja confianza")
    print(f"  degradado  : {manifest['degraded']} ({manifest['degraded_reason']})")
    for f in sorted(DESTINO.iterdir()):
        print(f"  {f.name:24s} {f.stat().st_size / 1024:7.1f} KB")


def _reflejar_en_dist() -> None:
    """Copia la demo a `web/dist/live/` si esa carpeta ya existe.

    Vite copia `public/` dentro de `dist/` **durante la compilación**, así que
    los datos generados después de compilar no llegan solos. El E2E se sirve con
    `vite preview`, que sirve `dist/`: sin esto, las peticiones de `.json`
    reciben el `index.html` del fallback SPA y los tests fallan con
    "Unexpected token '<'", que no se parece en nada a la causa real.

    Es idempotente y no crea `dist/` si no está: en desarrollo se sirve
    `public/` directamente y esta copia sobra.
    """
    dist = DESTINO.parents[1] / "dist" / "live"
    if not dist.parent.is_dir():
        return

    dist.mkdir(parents=True, exist_ok=True)
    for fichero in DESTINO.iterdir():
        if fichero.is_file():
            shutil.copy2(fichero, dist / fichero.name)
    print(f"Reflejada en {dist}")


def _escribe(gdf: gpd.GeoDataFrame, path: Path) -> None:
    """GeoJSON compacto. `to_file` de pyogrio no permite controlar el separador,
    así que se serializa a mano para no publicar 30 KB de espacios."""
    features = []
    for _, fila in gdf.iterrows():
        props = {
            k: (None if pd.isna(v) else v)
            for k, v in fila.drop("geometry").items()
            if not isinstance(v, (list, dict))
        }
        props = {k: (v.item() if hasattr(v, "item") else v) for k, v in props.items()}
        features.append({
            "type": "Feature",
            "properties": props,
            "geometry": fila.geometry.__geo_interface__ if fila.geometry else None,
        })
    path.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
