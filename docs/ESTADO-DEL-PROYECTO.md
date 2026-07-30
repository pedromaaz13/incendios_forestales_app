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

## 6 · Plan y próximos pasos

Ordenado por **valor para quien mira el mapa**, no por facilidad. Cada bloque
dice qué desbloquea y qué lo bloquea.

### El agujero que domina todo lo demás

Hoy en producción hay **79 incidentes y ninguno con parte oficial**. Todos se
publican como detección satelital sin confirmar, porque los cinco adaptadores
autonómicos siguen sin endpoint (C3 en `ERRORES-Y-SOLUCIONES.md`).

Eso significa que la mitad del producto no está funcionando. La fusión
oficial ↔ satélite —el módulo más trabajado del repo, con su emparejamiento por
tolerancia y su desempate por fuente— está probada y no tiene nada que fusionar.
Sin partes oficiales no hay nombre del incendio, ni estado (activo /
estabilizado / controlado), ni nivel IGR, ni medios desplegados. Solo puntos
calientes.

**Cualquier mejora de visualización rinde menos que conseguir un solo endpoint
autonómico.** Es el trabajo con más retorno del proyecto y el único que no puedo
hacer yo: requiere abrir el visor de una comunidad con las DevTools. El
procedimiento, con capturas, está en `COMO-CONECTAR-LAS-FUENTES.md`.

### Prioridad 1 · Un endpoint autonómico, cualquiera

Con uno solo ya se puede comprobar la fusión contra datos reales, que hoy solo
se ejercita con fixtures. Recomendado empezar por **Castilla y León** (`jcyl`):
su IDE publica GeoServer con WFS, que es un estándar y no una API propia.

### Prioridad 2 · Utilidad práctica sobre lo que ya tenemos

Ninguna necesita fuentes nuevas. Son cruces de capas que ya están publicadas:

1. **Viento sobre el incendio, en la ficha.** El dato ya está a 230 nodos y el
   incendio tiene coordenadas: interpolar y decir «viento del NO a 34 km/h, sopla
   hacia el SE» convierte la capa de viento en una respuesta a la pregunta que
   de verdad se hace la gente — *¿viene hacia mí?*
2. **Avisos que solapan con el incendio.** Ya se cruzan geométricamente: «hay
   aviso naranja de viento vigente en esta zona» es información que ninguna de
   las dos capas da por separado.
3. **Cortes de carretera cercanos en la ficha.** La capa ya distingue los
   declarados por incendio. Falta enseñar los que están junto a *este* incendio.
4. **Índice de propagación.** Temperatura, humedad y viento ya se publican
   juntos. Combinarlos es lo que hace comprensible por qué un incendio de 14 ha
   preocupa más que otro de 40. Cuidado: esto sería **nuestra estimación**, no un
   dato oficial, y habría que etiquetarlo como tal.

### Prioridad 3 · Fuentes nuevas que sí aportan

- **SEVIRI** (RF-P-02) · cadencia de **15 minutos** frente a las 2-4 pasadas
  diarias de VIIRS, a cambio de 3 km de píxel. Hoy la edad del dato llega a
  5 h; SEVIRI la bajaría a minutos. Es la mejora más grande posible en latencia,
  que es la razón de existir del proyecto. Sin clave: EUMETSAT LSA-SAF es
  público.
- **Rayos** · para tormenta seca, que es causa real de ignición. Blitzortung es
  abierto; conviene revisar su licencia antes.
- **Copernicus EMS** · perímetros cartografiados de incendios grandes, mucho más
  precisos que nuestra envolvente cóncava. Solo se activa en emergencias, así que
  cubre pocos incendios pero los importantes.

### Prioridad 4 · Alcance, no capacidad

- **Páginas SEO por incendio** (RF-P-13) — quien busca «incendio Sierra de Gata»
  en Google no llega hoy a nada
- **Alertas por correo** por zona
- **Histórico largo** — medido en ~13 KB/día, ~5 MB al año: git aguanta y R2 no
  es urgente. `ingest.yml` ya está escrito para R2 y desactivado esperando bucket

### Descartado, y por qué

- **Índice de riesgo de AEMET** — 404 y además son mapas PNG, no datos
  vectoriales. Un ráster no se cruza con un incendio
- **Derivar el riesgo de tormenta de variables crudas** (CAPE, humedad) — sería
  nuestra opinión sobre el tiempo, y este proyecto no tiene autoridad
  meteorológica. Los avisos CAP ya dan la declaración oficial
- **Framework de componentes en el frontend** — es un mapa con paneles
- **Base de datos en el camino de lectura** — regla dura

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
