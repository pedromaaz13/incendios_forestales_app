# Estado del proyecto

Foto del 30 de julio de 2026. Se actualiza al cerrar cada hito.

Todo lo que dice este documento está comprobado contra el repositorio o contra
la URL de producción en la fecha de arriba. Donde no hay comprobación posible,
se dice explícitamente.

---

## 1 · Qué es y dónde está

Visor público de incendios forestales activos en España. Cruza detecciones
satelitales con partes oficiales de los servicios autonómicos de extinción.

| | |
|---|---|
| Producción | https://pedromaaz13.github.io/incendios_forestales_app/ |
| Repositorio | `pedromaaz13/incendios_forestales_app` |
| Rama de producción | `main` |
| Frecuencia de actualización | cada 30 min (`publicar.yml`, cron) |
| Alojamiento | GitHub Pages · ficheros estáticos, sin base de datos |

La decisión de no tener base de datos en el camino de lectura es deliberada y
está en las reglas duras de `CLAUDE.md`: el frontend lee GeoJSON estáticos de
CDN. Sobrevive a un pico de tráfico sin que nadie toque nada, que es justo lo
que hace falta el día que haya un incendio grande.

---

## 2 · Qué se publica hoy

Recuento real de la última ejecución:

| Capa | Fichero | Registros |
|---|---|---|
| Incidentes | `incidents.geojson` | 37 |
| Focos de calor | `hotspots.geojson` | 2.412 |
| Perímetros estimados | `perimeters.geojson` | 126 |
| Viento | `wind.geojson` | 230 nodos |
| Calidad del aire | `aire.geojson` | 230 nodos |
| Carreteras cortadas | `trafico.geojson` | 477 |
| Avisos oficiales de AEMET | `avisos.geojson` | según boletín |
| Estado de fuentes | `sources.json` | 6 fuentes |
| Manifiesto | `manifest.json` | latencias y recuentos |

### Las dos latencias

Se publican siempre y por separado, y esto es el motivo de existir del
proyecto más que un detalle de implementación:

- **`data_age_seconds`** — cuánto hace que el satélite vio lo que estás mirando.
  Por sensor, no agregado.
- **`pipeline_age_seconds`** — cuánto hace que corrió el pipeline.

Mezclarlas induce a error: un pipeline que corrió hace 30 segundos puede estar
enseñando datos de hace nueve horas si el satélite no ha vuelto a pasar. El
frontend enseña los dos números y nunca uno solo.

---

## 3 · Estado por módulo

### Pipeline (Python)

| Módulo | Estado | Notas |
|---|---|---|
| `firms.py` | Completo | Ingesta NASA FIRMS · VIIRS ×3 + MODIS |
| `clean.py` | Completo | Confianza, máscara industrial, dedup espacio-temporal |
| `cluster.py` | Completo | ST-DBSCAN + perímetros cóncavos |
| `merge.py` | Completo | Precisión derivada del sensor |
| `enrich.py` | Completo | Geocoding inverso sobre la capa del IGN (8.220 municipios) |
| `export.py` | Completo | GeoJSON, PMTiles, Parquet |
| `validate.py` | Completo | Los 8 invariantes de la sección 4.4 |
| `wind.py` | Completo | 230 nodos · viento, temperatura y humedad |
| `aemet.py` | Completo | Avisos CAP 1.2 de Meteoalerta |
| `aire.py` | Completo | CAMS vía Open-Meteo, bandas EAQI oficiales |
| `trafico.py` | Completo | DGT DATEX II v3.7, feed nacional |
| `health.py` | Completo | Estado y antigüedad por fuente |
| `sources/` | Framework listo | **5 endpoints sin descubrir** |

### Frontend (TypeScript + MapLibre, sin framework)

| Pieza | Estado |
|---|---|
| Mapa base y capas | Completo |
| Agrupación numérica que se dispersa al hacer zoom | Completo |
| Ficha de incendio | Completo · fuente, superficie, evolución |
| Evolutivo diario global | Completo |
| Evolutivo por incendio | Completo |
| Filtros (período, confianza, origen, sensor) | Completo |
| Viento animado con partículas | Completo |
| Leyenda por intensidad y por confirmación | Completo |
| Panel de estado de fuentes | Completo |
| Aviso permanente del 112 | Completo, no ocultable |

---

## 4 · Pruebas

```
361 pruebas pasan · 3 saltadas · cobertura 93,20 %   (mínimo exigido: 85 %)
62 pruebas E2E en Playwright
```

Los 3 `skip` no son deuda escondida. Cada uno cita el requisito que espera:
dependen de la capa del IGN en un caso y de decisiones de un hito posterior en
los otros dos.

La suite corre sin red. Toda fuente externa tiene su fixture de regresión en
`tests/fixtures/`, y cuando un parseo falla en producción el payload que lo
rompió se convierte en fixture **antes** de arreglar el código.

---

## 5 · Lo que falta por arreglar

### 5.1 · ~~La precisión miente en los incendios que solo vio MODIS~~ · RESUELTO

`merge.py` asignaba 375 m —el píxel de VIIRS— a todos los incendios satelitales.
El de MODIS es 1 km, así que 8 de 44 incidentes publicaban una incertidumbre
casi tres veces menor que la real, y sobre ese radio la ficha afirma que «el
incendio puede estar en cualquier punto de su interior».

Resuelto derivando la precisión del **mejor** sensor que vio cada incendio, a
partir del campo `sensors`. Tres pruebas de regresión fijan los casos VIIRS,
MODIS y mixto; se verificó que la de MODIS falla sin el arreglo.

### 5.2 · ~~El scope `workflow` del token bloquea Actions~~ · RESUELTO

Ni el token del agente ni el cacheado en el llavero de macOS tenían el scope
`workflow`, así que ningún fichero de `.github/workflows/` podía subirse. Y una
push es atómica: un solo commit que tocara un workflow tumbaba el lote entero.

Resuelto con `gh auth refresh -h github.com -s workflow` más
`gh auth setup-git`, que hace que git use el token de `gh` en lugar del que
estaba cacheado en el llavero. `sondear.yml` está en `main` y `publicar.yml` ya
pasa `AEMET_API_KEY` al pipeline.

### 5.3 · Cinco endpoints autonómicos sin descubrir

`112cv`, `infocam`, `jcyl` y dos más siguen en `disabled` con el motivo
«endpoint sin descubrir». Los adaptadores tienen la URL vacía **a propósito**:
una URL inventada devuelve 404 en silencio y eso se lee como «hoy no hay
incendios», que es el fallo más peligroso de este sistema.

Se desbloquea abriendo el visor autonómico con las DevTools en la pestaña Red y
copiando la petición que devuelve los incendios. El procedimiento está en
`docs/COMO-CONECTAR-LAS-FUENTES.md`.

### 5.4 · EFFIS caído

Lleva más de tres días respondiendo `msOracleSpatialLayerOpen(): Cannot create
OCI Handlers`. Es un fallo de su servidor, no nuestro. `scripts/vigilar_effis.py`
comprueba si ha vuelto.

---

## 6 · Lo que falta por desarrollar

Por orden de dependencia, no de valor.

### ~~Bloque 1 · AEMET~~ · HECHO

Avisos oficiales de meteorología adversa (Meteoalerta, CAP 1.2) publicados como
capa, con fixture de regresión y 18 pruebas. Se filtran a nivel amarillo o
superior y a los siete fenómenos que afectan a un incendio; se descartan los
expirados.

Los avisos CAP son mejor fuente que derivar el riesgo de variables crudas: son
la declaración oficial del organismo competente, no una inferencia nuestra.

El **índice de riesgo de incendio** queda fuera por dos razones: su endpoint
responde `404 · No hay datos que satisfagan esos criterios`, y además son mapas
PNG, no datos vectoriales. Un ráster no se puede consultar por municipio ni
cruzar con un incendio.

### ~~Bloque 2 · Temperatura~~ · HECHO

Temperatura y humedad relativa sobre los mismos 230 puntos, en la misma llamada
a Open-Meteo que ya se hacía para el viento. Viajan juntas porque se leen
juntas: 38 ºC con 15 % de humedad y 40 km/h es la combinación que propaga un
incendio, y ninguno de los tres números por separado lo dice.

### Bloque 3 · Histórico para el evolutivo

Hoy el evolutivo se reconstruye de los focos de las últimas 72 h. Para series
más largas hace falta acumular. Medido: ~13 KB por día, ~5 MB al año, así que
**git aguanta perfectamente** y Cloudflare R2 no es urgente. `ingest.yml` ya
está escrito para R2 y desactivado a la espera del bucket.

### Bloque 4 · Sin empezar

- **SEVIRI** (RF-P-02) — 15 min de cadencia frente a las pasadas de VIIRS, a
  cambio de 3 km de píxel
- **Páginas SEO** (RF-P-13) — una por incendio, indexable
- **Alertas por correo**

---

## 7 · Reglas que no se negocian

Están en `CLAUDE.md` y se repiten aquí porque explican decisiones del código
que de otro modo parecen arbitrarias.

1. **No se inventan endpoints.** Una URL falsa devuelve 404 en silencio y se
   lee como ausencia de incendios.
2. **No se publican salidas vacías.** Cero registros donde ayer había cientos es
   un fallo de la fuente, no ausencia de incendios. Se aborta sin sobrescribir.
3. **Un fallo de fuente no tumba el pipeline.**
4. **La latencia se publica siempre**, las dos, separadas.
5. **Nada de base de datos en el camino de lectura.**
6. **Ante la duda, no se muestra.** Esto lo mira gente asustada buscando si arde
   algo cerca de su casa. La interfaz dice «estimación» y «detección», nunca
   verbos de certeza, y el aviso de que no sustituye al 112 no se oculta en
   ninguna resolución.

---

## 8 · Cómo trabajar sobre esto

```bash
pytest                    # suite completa
pytest --cov              # falla por debajo del 85 %
pytest tests/test_invariants.py    # obligatorio si tocas merge.py o export.py

PYTHONPATH=src python scripts/smoke_test.py     # humo, sin red

cd web && npx playwright test                   # E2E
```

Orden de lectura para alguien que llega nuevo: `docs/ESPECIFICACION.md`
(el contrato), `README.md` (decisiones y su porqué), `src/incendios/config.py`
(todos los parámetros en un sitio) y `src/incendios/merge.py` entero antes de
tocar nada de fusión.
