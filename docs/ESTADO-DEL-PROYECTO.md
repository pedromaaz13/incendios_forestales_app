# Estado del proyecto

Foto del 3 de agosto de 2026. Se actualiza al cerrar cada hito.

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
| Incidentes | `incidents.geojson` | 48 |
| Focos de calor | `hotspots.geojson` | fiables + confianza baja etiquetada |
| Perímetros estimados | `perimeters.geojson` | uno por incendio agrupado |
| Viento, temperatura y humedad | `wind.geojson` | 230 nodos |
| Calidad del aire | `aire.geojson` | 230 nodos |
| Carreteras cortadas | `trafico.geojson` | DGT, feed nacional |
| Avisos oficiales de AEMET | `avisos.geojson` | según boletín |
| Estado de fuentes | `sources.json` | 8 fuentes |
| Manifiesto | `manifest.json` | latencias y recuentos |

**Dos fuentes oficiales activas**: `jcyl` (11 partes) y `112cv` (7). El resto
sigue sin endpoint.

Cada incidente publica, además de su posición e intensidad:

| Campo | Qué dice | De dónde sale |
|---|---|---|
| `status` · `status_origen` | Estado, y **quién lo afirma**. Nulo sin parte oficial | Parte autonómico |
| `igr_level` | Situación operativa 0-2 | Parte autonómico |
| `resources_text` | Medios actuando | Parte autonómico |
| `detalle_oficial` | Dónde está, con las palabras del operador | 112 CV |
| `sensors` · `position_precision_m` | Qué satélite lo vio y con cuánta incertidumbre | FIRMS |
| `nucleo_cercano` · `_km` · `_habitantes` | El pueblo habitado más cercano | IGN · núcleos |
| `suelo_clase` · `suelo_tipo` | Sobre qué arde: monte, cultivo, urbano | CORINE 2018 |
| `viento_*` · `temp_c` · `humedad_pct` | Condiciones observadas ahí | Open-Meteo, interpolado |
| `aviso_*` | Aviso de AEMET vigente sobre la comarca | AEMET Meteoalerta |
| `cortes_cerca` · `_por_incendio` | Accesos cortados a menos de 15 km | DGT |
| `focos_recientes` · `crecimiento_ha_h` | Superficie nueva ya detectada en 6 h | FIRMS |
| `ultima_observacion_h` | Cuánto hace que se vio calor ahí | FIRMS |

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
| `suelo.py` | Completo | Uso del suelo por incendio · CORINE 2018 |
| `sources/jcyl.py` | Completo | INFORCYL · coordenadas UTM, medios, nivel INFOCAL |
| `sources/cv112.py` | Completo | 112 CV · filtra incidencias que no son incendio |
| `aire.py` | Completo | CAMS vía Open-Meteo, bandas EAQI oficiales |
| `trafico.py` | Completo | DGT DATEX II v3.7, feed nacional |
| `health.py` | Completo | Estado por **edad del dato**, no de la descarga |
| `sources/` | 2 de 5 conectadas | **`infocam` sin descubrir**; `bombers` e `infoca` sin feed público conocido |

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
481 pruebas pasan · 3 saltadas · cobertura 94,12 %   (mínimo exigido: 85 %)
81 pruebas E2E en Playwright
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

### 5.3 · Un endpoint autonómico sin descubrir

`infocam` sigue con la URL vacía **a propósito**: una URL inventada devuelve 404
en silencio y eso se lee como «hoy no hay incendios», que es el fallo más
peligroso de este sistema.

`jcyl` se descubrió el 30-07 y `112cv` el 03-08. `bombers` e `infoca` se dan por
**no disponibles**, no por pendientes: no hay indicios de que exista un feed
público en tiempo real de esas dos comunidades.

### 5.4 · EFFIS caído

Lleva más de tres días respondiendo `msOracleSpatialLayerOpen(): Cannot create
OCI Handlers`. Es un fallo de su servidor, no nuestro. `scripts/vigilar_effis.py`
comprueba si ha vuelto.

---

## 6 · Plan y próximos pasos

Actualizado el 03-08-2026. Los bloques 0, 1, 2 y 4 están **cerrados y en
producción**; el 3 está bloqueado y el 5 sin empezar.

---

### ~~Bloque 0 · Honestidad del estado de las fuentes~~ · HECHO

**Qué hace.** `health.py` responde ahora a **dos preguntas separadas**: ¿la
descarga funcionó? → `error`. ¿el dato es fresco? → `stale`. Antes solo miraba la
primera.

**Qué conseguimos.** El 31-07-2026 FIRMS dejó de servir VIIRS —cero filas en 24 h
para los tres satélites, mientras MODIS daba once focos— y el panel decía
`ok · 883 registros · hace 15 s`, porque la descarga del archivo de tres días
seguía funcionando. Ahora dice **«responde, pero sin datos nuevos desde hace
20 h»**.

`max_data_age_seconds` se declara por fuente y es **opcional**: 12 h para las
polares, 2 h para SEVIRI, y nada para las oficiales — que la Junta no publique un
incendio nuevo en 20 h es una buena noticia, no una avería.

`stale_reason` dice **quién** falló, porque «rancio» a secas no distingue «no
hemos podido descargar» de «la fuente dejó de publicar», y solo el primero se
arregla desde aquí.

### ~~Bloque 1 · Las dos latencias, explicadas~~ · HECHO

**Qué hace.** Un `title` en cada número de la cabecera, el desglose por sensor en
vez de solo el peor, y el intervalo declarado corregido a 30 min.

**Qué conseguimos.** Con un solo número, «19 h 41 min» se leía como que todo
estaba viejo, cuando MODIS tenía 5,6 h y lo parado era VIIRS. Ahora se ve
**«VIIRS 19 h · MODIS 5,6 h»**: una alarma difusa pasa a ser un diagnóstico.

El titular sigue siendo el peor caso, que es la regla del proyecto: enseñar el
mejor sería tranquilizar sin fundamento.

### ~~Bloque 2 · Endpoints autonómicos~~ · HECHO A MEDIAS

**Castilla y León** (30-07) y **112 Comunitat Valenciana** (03-08) conectadas.
`infocam` sigue sin descubrir.

**Qué conseguimos con JCyL.** Estado declarado, nivel operativo INFOCAL, medios
actuando y descarte de falsas alarmas. Su trampa: los campos se llaman `latitud`
y `longitud` pero son **metros UTM**; sin convertir, esa comunidad no habría
aparecido nunca y el mapa habría seguido funcionando.

**Qué conseguimos con el 112 CV.** La coordenada más precisa de todas (±100 m) y
la **dirección con las palabras del operador**: «AP-7 Km364 >sur». Sitúa el fuego
respecto a una carretera, que es como la gente localiza las cosas, y no se puede
derivar de una coordenada.

Su trampa: **es un feed de incidencias, no de incendios**. De 58 registros, 15
eran fuego; el resto accidentes, contaminación y salvamentos. Publicar un
accidente de tráfico como incendio forestal sería de lo peor que puede pasar en
este visor, así que hay ocho pruebas solo sobre su taxonomía.

### Bloque 3 · Sentinel-3 · **BLOQUEADO**

Sondeado el 03-08: el catálogo STAC de Copernicus responde 200 pero **no publica
ninguna colección de producto de fuego**, solo de imagen. Corrige lo que se dijo
antes: no es que dé 403, es que ahí no está. El producto FRP va por otra vía que
sigue requiriendo registro.

Sigue mereciendo la pena: hoy dependemos de dos sensores y cuando uno se cae —ya
ha pasado— el visor se queda con 20 h de retraso.

### ~~Bloque 4 · Uso del suelo~~ · HECHO

**Qué hace.** Consulta CORINE Land Cover 2018 por coordenada y etiqueta cada
incendio con el terreno sobre el que cae.

**Qué conseguimos.** De los 48 incidentes publicados el 03-08: **16 forestales,
17 agrícolas, 14 urbanos**. Un tercio de lo que se publicaba como «incendio» cae
sobre cultivo —casi siempre quema de rastrojo— y otro tercio sobre suelo urbano,
que suele ser antorcha industrial o falsa detección.

**Lo que deliberadamente NO hace: filtrar.** La tentación es quitar los agrícolas
del mapa y sería un error — una quema de rastrojo que se descontrola es cómo
empiezan muchos incendios forestales. Se etiqueta y quien mira decide. Hay un
test que lo fija.

Viable porque el servicio de la Agencia Europea de Medio Ambiente es **público y
soporta consulta por punto**: descargar la capa europea serían decenas de
gigabytes para mirar unas decenas de coordenadas.

---

## Lo que queda

### Bloqueado en ti

1. **`infocam` (Castilla-La Mancha).** Ni su portal de datos abiertos —la API
   CKAN da 404— ni las rutas del sitio de la Junta exponen un visor localizable.
   Necesita DevTools sobre su visor, como se hizo con INFORCYL.
2. **Registro para el producto de fuego de Sentinel-3.**

### Bloqueado en terceros

- **EFFIS**, caído desde el 27-07 con un fallo de su Oracle. Aportaría perímetros
  cartografiados y el índice FWI oficial. `scripts/vigilar_effis.py` avisa.
- **`bombers` e `infoca`**: la evidencia apunta a que no existe feed público en
  tiempo real. No son tareas pendientes, son fuentes que no hay.

### Pendiente, no bloqueado

1. **Natura 2000** — «arde dentro de un parque natural». Con el patrón de CORINE
   ya montado es directo: mismo tipo de servicio, misma forma de consulta.
2. **Páginas SEO por incendio** — ahora tienen nombre, estado y nivel, así que ya
   tiene sentido. Quien busca «incendio Villafranca del Bierzo» no llega hoy.
3. **Alertas por correo** por zona.
4. **Histórico largo** — 13 KB/día, ~5 MB al año: git aguanta y R2 no es urgente.

### Descartado en firme

- **Índice de riesgo de AEMET** — 404 y son mapas PNG. Un ráster no se cruza con
  un incendio.
- **Derivar el riesgo de tormenta de variables crudas** — sería nuestra opinión
  sobre el tiempo; los avisos CAP ya dan la declaración oficial.
- **Índice de propagación propio** — sería una predicción nuestra ante alguien
  que mira si arde algo cerca de su casa.
- **Consumir la API del visor de referencia** — dependencia de un tercero y
  aprovecharse de su trabajo. Sección 12.3.
- **Reintento a nivel de job para FIRMS** — falla ~3 de cada 13 ejecuciones por
  la red del runner de GitHub. El sistema aborta limpio sin publicar y el cron
  recupera en 30 min.

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
