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
| Viento, temperatura y humedad | `wind.geojson` | 230 nodos |
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
| `validate.py` | Completo | Los 8 invariantes de 4.4 más el 9: ningún estado sin quien lo afirme |
| `wind.py` | Completo | 230 nodos · viento, temperatura y humedad |
| `aemet.py` | Completo | Avisos CAP 1.2 de Meteoalerta |
| `contexto.py` | Completo | Viento, avisos, cortes, ritmo y distancia a población |
| `aire.py` | Completo | CAMS vía Open-Meteo, bandas EAQI oficiales |
| `trafico.py` | Completo | DGT DATEX II v3.7, feed nacional |
| `health.py` | Completo | Estado y antigüedad por fuente |
| `sources/` | Framework listo | **5 endpoints sin descubrir** |

### Frontend (TypeScript + MapLibre, sin framework)

| Pieza | Estado |
|---|---|
| Mapa base y capas | Completo |
| Agrupación numérica que se dispersa al hacer zoom | Completo |
| Ficha de incendio | Completo · fuente, superficie, evolución, condiciones en la zona |
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
casi tres veces menor que la real. Resuelto derivándola del **mejor** sensor que
vio cada incendio, con tres pruebas de regresión.

### 5.2 · ~~El scope `workflow` del token bloquea Actions~~ · RESUELTO

`gh auth refresh -h github.com -s workflow` más `gh auth setup-git`. El segundo
es la mitad que se olvida: sin él git sigue tirando del token del llavero.

### 5.2b · ~~«Activo» se afirmaba sin que nadie lo declarara~~ · RESUELTO

El fallo de más alcance que ha tenido el proyecto: los 79 incendios de producción
publicaban `status = "activo"` y la interfaz los pintaba en rojo con esa palabra,
sin que ningún servicio de extinción lo hubiera declarado. Internamente solo
significaba «detectado dentro de la ventana reciente», y con 6 h de antigüedad ese
fuego puede estar apagado.

Ahora `status` es **nulo** sin parte oficial, `status_origen` dice quién lo
afirma, y la interfaz enseña «Calor detectado hace 6 h» en gris. El **invariante
9** aborta la publicación si alguien vuelve a rellenar el hueco con un valor por
defecto.

### 5.2c · ~~La capa de focos nacía sin filtro~~ · RESUELTO

Se monta de forma asíncrona y `aplicarFiltros` corría antes de que existiera.
FIRMS se pide con 3 días de margen: **579 de los 1.182 focos publicados tenían
más de 24 h** y se pintaban con el control puesto en «1 día». No fallaba nada
visible; el mapa enseñaba más focos de los que decía.

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

Actualizado el 31-07-2026. Ordenado por **orden de desarrollo**, no por deseo:
cada bloque dice qué desbloquea, qué lo bloquea y cuánto cuesta.

---

### Bloque 0 · Honestidad del estado de las fuentes · **AHORA**

Lo primero porque es un **fallo silencioso activo en producción**, del tipo exacto
que este proyecto existe para no cometer.

**Qué pasa.** El 31-07-2026, FIRMS dejó de servir VIIRS: cero filas en 24 h para
los tres satélites, mientras MODIS seguía dando 11 focos. VIIRS es más sensible
que MODIS —375 m frente a 1 km— así que cero detecciones cuando MODIS ve once no
es posible: su feed está caído en origen.

El panel de fuentes, mientras tanto, decía:

```
NASA FIRMS · VIIRS   ok · edad declarada 15 s · 883 registros
```

**Por qué miente.** `health.py` guarda en `last_success_at` **cuándo conseguimos
descargar**, no **cuándo se tomó el dato**. Como se pide una ventana de 3 días,
FIRMS siempre devuelve filas de su archivo, la descarga siempre funciona, y la
fuente parece sana indefinidamente. Una fuente que dejó de publicar aparece
correcta porque seguimos bajando su archivo viejo.

**Qué hacer.**

1. Añadir `data_age_seconds` a `SourceHealth`: la antigüedad del **dato más
   reciente**, no la de la descarga.
2. `status()` pasa a `stale` cuando el dato supera la cadencia esperada del
   sensor. Hoy VIIRS saldría en ámbar con «sin datos nuevos desde hace 20 h».
3. Un aviso en el panel cuando una fuente crítica lleva más de una cadencia sin
   publicar.

Coste: media hora. **Convierte una avería invisible en visible.**

---

### Bloque 1 · Las dos latencias, explicadas · **AHORA**

Los dos números de la cabecera son la razón de ser del proyecto y **no se
entienden sin explicación**. Lo comprobó quien lo construyó, que dudó de qué
significaba cada uno.

- **«Datos satelitales · 19 h 41 min»** — cuándo lo vio un satélite. No depende
  de nosotros.
- **«Actualización · hace 24 min»** — cuándo corrió nuestro pipeline.

Mezclarlos es el error que este visor existe para no cometer: un pipeline que
corrió hace 24 minutos puede estar enseñando lo que se vio ayer.

**Qué hacer.**

1. Una nota corta o un `title` en cada número explicando cuál es cuál.
2. Enseñar la antigüedad **por sensor**, no solo la peor. Hoy se lee «19 h 41» y
   parece que todo está viejo, cuando MODIS tiene 5,6 h.
3. Corregir «refresco cada 10 min» → **30 min**, que es lo que hace el cron. El
   texto se quedó de cuando era otro intervalo.

Coste: una hora. Es la diferencia entre un número alarmante y un número que
informa.

---

### Bloque 2 · Un endpoint autonómico más · **BLOQUEADO EN TI**

**CV112** primero. Es el que más aporta de los dos que quedan: coordenada de
±100 m —la más precisa de todas las fuentes— y la descripción en texto libre del
112, que sitúa el fuego respecto a una carretera, que es como la gente localiza
las cosas.

Después **FIDIAS** (Castilla-La Mancha).

**`bombers` e `infoca` se dan por no disponibles**, no por pendientes: el feed
del visor de referencia no los lleva, teniendo incendios en Andalucía y Cataluña
detectados solo por satélite, y el portal catalán publica agregados mensuales sin
coordenadas.

Procedimiento en `COMO-CONECTAR-LAS-FUENTES.md`. Con Castilla y León ya
funcionando, cada endpoint nuevo es media hora de trabajo: el marco está hecho.

---

### Bloque 3 · Sentinel-3 · **BLOQUEADO EN REGISTRO**

La caída de VIIRS del 31-07 es el argumento: **hoy dependemos de dos sensores y
uno se ha caído**, dejando el visor con 20 horas de retraso. Un tercero convierte
una avería en una molestia.

Sentinel-3 antes que SEVIRI, y esto corrige lo que se dijo al principio de la
sesión:

| | Pasadas | Detecta incendios de |
|---|---|---|
| Sentinel-3 | ~4/día más | ~30 ha, como MODIS |
| SEVIRI | continuo | solo grandes (~900 ha/píxel) |

Con una mediana de 28 ha por incendio, SEVIRI apenas vería ninguno de los que
tenemos. Sondeado el 30-07: el catálogo STAC de Copernicus responde 200 pero
`resto/api` da **403**, así que hace falta registro.

---

### Bloque 4 · Contexto que cambia la lectura

**CORINE Land Cover.** Separaría el ruido agrícola del incendio forestal: un
«incendio» sobre cultivo en julio es probablemente quema de rastrojo. Explicaría
buena parte de los incendios de un solo foco. Con el caché que ya monta los
núcleos de población, el camino está trillado.

**Espacios protegidos (Natura 2000).** «Arde dentro de un parque natural» es
información que ninguna otra capa da.

---

### Bloque 5 · Alcance

**Páginas SEO por incendio** (RF-P-13). Tiene más sentido ahora que los incendios
tienen nombre, estado y nivel: quien busca «incendio Villafranca del Bierzo» no
llega hoy a nosotros.

**Alertas por correo** por zona.

**Histórico largo.** Medido: 13 KB/día, ~5 MB al año. Git aguanta y Cloudflare R2
no es urgente. `ingest.yml` está escrito para R2 y desactivado esperando bucket.

---

### Descartado en firme

No vuelven a la lista, y cada uno con su motivo:

- **Índice de riesgo de AEMET** — responde 404 y además son mapas PNG. Un ráster
  no se cruza con un incendio ni se consulta por municipio.
- **Derivar el riesgo de tormenta de variables crudas** (CAPE, humedad) — sería
  nuestra opinión sobre el tiempo, y este proyecto no tiene autoridad
  meteorológica. Los avisos CAP ya dan la declaración oficial.
- **Índice de propagación propio** — combinar temperatura, humedad y viento para
  afirmar que un incendio se propagará es una predicción nuestra ante alguien que
  mira si arde algo cerca de su casa. Cuando EFFIS vuelva, su `fwi_nuts5.fwi` es
  el índice oficial por municipio.
- **Consumir la API del visor de referencia** — nos haría depender de la
  infraestructura de un tercero y sería aprovecharnos de su trabajo. Su feed
  sirve como mapa del tesoro, no como fuente. Sección 12.3.
- **Reintento a nivel de job para FIRMS** — 3 fallos de 13 por la red del runner
  de GitHub. El sistema aborta limpio sin publicar y el cron siguiente recupera
  en 30 min. Añadir complejidad para ganar media hora de frescura en una de cada
  cuatro ejecuciones no compensa.
- **Framework de componentes en el frontend** — es un mapa con paneles.
- **Base de datos en el camino de lectura** — regla dura.

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
