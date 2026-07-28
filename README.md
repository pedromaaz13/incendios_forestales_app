# incendios-es

### 🔥 **[Ver el mapa en vivo →](https://pedromaaz13.github.io/incendios_forestales_app/)**

[![publicar](https://github.com/pedromaaz13/incendios_forestales_app/actions/workflows/publicar.yml/badge.svg)](https://github.com/pedromaaz13/incendios_forestales_app/actions/workflows/publicar.yml)
[![ci](https://github.com/pedromaaz13/incendios_forestales_app/actions/workflows/ci.yml/badge.svg)](https://github.com/pedromaaz13/incendios_forestales_app/actions/workflows/ci.yml)

Visor de focos activos en España a partir de detecciones satelitales de anomalías térmicas.
Pipeline serverless, sin base de datos, coste de infraestructura ~0 €.

Se republica solo **cada 30 minutos** desde GitHub Actions.

> Estas detecciones **no son información oficial de emergencias**. Un hotspot es un píxel
> con anomalía térmica, no un incendio confirmado. Para incidencias en curso, 112.

## Dónde vive cada cosa

| | |
|---|---|
| **Visor** | <https://pedromaaz13.github.io/incendios_forestales_app/> |
| Datos publicados | [`/live/manifest.json`](https://pedromaaz13.github.io/incendios_forestales_app/live/manifest.json) · [`incidents.geojson`](https://pedromaaz13.github.io/incendios_forestales_app/live/incidents.geojson) · [`sources.json`](https://pedromaaz13.github.io/incendios_forestales_app/live/sources.json) |
| Publicación | [`.github/workflows/publicar.yml`](.github/workflows/publicar.yml) — cron `*/30` |
| Integración continua | [`.github/workflows/ci.yml`](.github/workflows/ci.yml) |

---

## Cómo arrancarlo

### En un comando

```bash
export FIRMS_MAP_KEY=tu-clave     # o pégala en un fichero .env
./arrancar.sh                     # abre http://localhost:5173
```

Descarga los incendios reales de las últimas horas, ejecuta el pipeline
completo y levanta el visor. No necesita Cloudflare, R2, Actions ni ningún
servidor: todo local. Sin clave arranca igual, con datos de demostración y un
aviso permanente que lo declara.

### Pipeline, paso a paso

```bash
pip install -r requirements.txt
pytest --cov                                    # 232 pruebas, cobertura ≥ 85 %
PYTHONPATH=src python scripts/smoke_test.py     # prueba de humo sin red
```

Para correr contra datos reales hace falta `FIRMS_MAP_KEY` (gratis, registro por
correo en <https://firms.modaps.eosdis.nasa.gov/api/map_key/>):

```bash
export FIRMS_MAP_KEY=...
PYTHONPATH=src python -m incendios.pipeline -v
```

### Frontend

```bash
PYTHONPATH=src python scripts/build_demo_data.py   # genera web/public/live/
cd web && npm install && npm run dev
```

`build_demo_data.py` produce artefactos sintéticos con el contrato exacto de las
secciones 4.1–4.3, para que el visor se pueda mirar sin claves ni red. Van
marcados con `demo: true` en el manifiesto y el frontend pinta una banda azul
permanente: un juego de demostración indistinguible de la producción sería justo
el engaño que este proyecto existe para no cometer.

## Estado

| Módulo | Estado |
|---|---|
| `firms.py` · ingesta NASA FIRMS | Completo, probado |
| `clean.py` · filtros y máscara industrial | Completo, probado |
| `cluster.py` · ST-DBSCAN y perímetros | Completo, probado |
| `merge.py` · fusión oficial ↔ satélite | Completo, probado |
| `validate.py` · 8 invariantes (RF-P-14) | Completo, probado |
| `health.py` · `sources.json` (RF-P-10) | Completo, probado |
| `publish.py` · atomicidad y vaciado (RF-P-11) | Completo, probado |
| `build.py` · manifiesto y contrato web | Completo, probado |
| `wind.py` · viento Open-Meteo (RF-P-09) | Completo, probado |
| `enrich.py` · geocoding inverso | Completo · `scripts/preparar_municipios.py` valida y prepara la capa |
| `export.py` · GeoJSON, PMTiles, Parquet | Completo, probado |
| `web/` · frontend v1 (hito 2) | Completo: RF-F-01…07, 10…13 |
| `sources/` · adaptadores oficiales | Framework probado, **endpoints sin descubrir** |
| SEVIRI, EFFIS, SEO, alertas | Sin empezar |

**Lo único que bloquea la puesta en producción con datos reales** son los cinco
endpoints autonómicos de RF-P-03. No se inventan: el procedimiento para
descubrirlos está en `docs/descubrimiento-fuentes.md`. Mientras tanto salen como
`disabled` en el panel de estado, que es honesto, en lugar de como "0 incendios",
que no lo sería.

---

## Arquitectura

```
┌─ GitHub Actions (cron */10) ──────────────────────────────────┐
│                                                               │
│  firms.py    4 sensores × 3 bboxes en paralelo → CSV          │
│      ↓       normalización de esquema VIIRS/MODIS             │
│  clean.py    filtro de confianza                              │
│      ↓       máscara de exclusión industrial (buffer 1.2 km)  │
│      ↓       dedup espacio-temporal (celda 200 m × 1 h)       │
│  cluster.py  ST-DBSCAN → fire_id estable por hash             │
│      ↓       agregados por incendio + concave hull            │
│  enrich.py   sjoin con límites municipales del IGN            │
│  export.py   GeoJSON · PMTiles · Parquet particionado         │
└───────────────────────────┬───────────────────────────────────┘
                            │  aws s3 sync (endpoint R2)
                            ▼
              Cloudflare R2  ──── CDN ────►  MapLibre GL (web/)
              live/  (max-age 120)
              history/ (immutable)
```

Sin backend en runtime: el navegador lee ficheros estáticos desde CDN. El único
proceso es el cron, y GitHub Actions lo cubre en el tier gratuito.

## Decisiones que importan

**ST-DBSCAN por proyección del eje temporal.** En lugar de implementar ST-DBSCAN,
se proyecta a metros (UTM 30N en península/Baleares, REGCAN95 UTM 28N en Canarias)
y se convierte el tiempo a metros equivalentes con `eps_m / eps_hours`. Un DBSCAN
euclídeo en 3D es entonces equivalente, y aprovecha el ball-tree de scikit-learn.

**Los `fire_id` son hashes, no autoincrementales.** Se derivan de
`sha1(centroide_redondeado + fecha_inicio)`, así un mismo incendio conserva su
identificador entre ejecuciones y permite hacer seguimiento sin estado.

**La máscara de falsos positivos es el trabajo real.** VIIRS detecta antorchas
de refinería, incineradoras, hornos cerámicos, centrales térmicas y reflejos
especulares sobre invernaderos. `config/exclusions.geojson` es una semilla manual;
`scripts/build_exclusions.py` la amplía de forma empírica sobre el histórico
(celdas con detección en más de N días distintos del año no son incendios forestales).

**La latencia se publica, no se oculta.** `manifest.json` expone
`data_age_minutes` y el frontend lo pone como dato principal de la cabecera. Los
datos NRT tienen entre 60 min y 3 h de retraso; presentarlos como "tiempo real"
sin más es lo que convierte un visor en desinformación.

**La superficie es una estimación etiquetada.** `n_hotspots × 14.06 ha` es una
cota inferior grosera basada en el tamaño de píxel VIIRS. El `hull_area_ha` del
perímetro es más realista pero sobreestima en incendios discontinuos. Ninguno
sustituye a los perímetros de Copernicus EMS.

## Puesta en marcha

```bash
git clone <tu-repo> && cd incendios-es
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # pega tu FIRMS_MAP_KEY
export FIRMS_MAP_KEY=...  # gratis en firms.modaps.eosdis.nasa.gov/api/map_key/
export PYTHONPATH=src

python -m incendios.pipeline -v
```

Salidas en `data/out/`. Para el frontend:

```bash
cd web && python -m http.server 8000
# http://localhost:8000/?lat=40.5&lon=-3.7&zoom=9
```

PMTiles es opcional: si `tippecanoe` no está en el PATH, el paso se omite y el
frontend sigue funcionando con GeoJSON.

## Despliegue

1. **R2**: crea el bucket, activa acceso público y apunta un dominio.
2. **Secrets del repo**: `FIRMS_MAP_KEY`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`.
3. **Variables del repo**: `R2_BUCKET`, `R2_ACCOUNT_ID`.
4. **Frontend**: Cloudflare Pages apuntando a `web/`, con
   `window.DATA_BASE = "https://<tu-dominio>/live"` inyectado antes del script.

## Enriquecimiento opcional

| Capa | Fuente | Dónde |
|---|---|---|
| Límites municipales | IGN, Centro de Descargas (`recintos_municipales_inspire`) | `config/municipios.geojson` |
| Perímetros oficiales | Copernicus EFFIS / EMS Rapid Mapping | capa adicional |
| Meteorología | AEMET OpenData (viento, HR, riesgo) | popup de la ficha |
| Severidad post-incendio | Sentinel-2 L2A B8A/B12 → dNBR | job semanal aparte |

## Hoja de ruta

- [ ] Máscara automática tras una temporada completa de histórico
- [ ] Perímetros de Copernicus EFFIS como capa de contraste
- [ ] Sentinel-3 SLSTR FRP para reducir el hueco entre pasadas VIIRS
- [ ] MTG-I FCI (geoestacionario, ~10 min) — cambia la latencia de horas a minutos
- [ ] Vista temporal: reproducción de la progresión de un incendio
- [ ] `dbt` + DuckDB sobre el histórico para métricas de temporada

## Licencia de datos

NASA FIRMS: dominio público, se pide atribución.
Copernicus: licencia abierta con atribución.
Basemap: © OpenStreetMap contributors, © CARTO.
