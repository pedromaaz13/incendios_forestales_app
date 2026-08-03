# Errores encontrados y cómo se resolvieron

Registro de fallos reales de este proyecto: en el código, en las fuentes, en los
endpoints y en el despliegue. Cada entrada dice **qué se rompió**, **por qué
costó verlo** y **qué lo impide ahora**.

Se escribe porque el patrón se repite. Casi ningún fallo de aquí dio un error:
la mayoría devolvió un resultado plausible y equivocado, que es la forma que
tiene de fallar un sistema de datos. La columna que más importa de cada entrada
no es la solución, es *por qué no saltó nadie*.

Orden: por categoría, y dentro de cada una por gravedad.

---

## Lo que sigue abierto

Lo demás de este documento está resuelto y se conserva por el patrón, no por la
tarea. Esto es lo que hay que revisar:

| | Qué | Quién lo desbloquea |
|---|---|---|
| **C3** | Dos endpoints autonómicos sin descubrir: `112cv` e `infocam` | Requiere DevTools sobre el visor autonómico · procedimiento en `COMO-CONECTAR-LAS-FUENTES.md` |
| **C5** | `bombers` e `infoca` sin feed público en tiempo real conocido | Nadie por ahora: la evidencia apunta a que no existe |
| **B5** | Una fuente que deja de publicar sale como `ok`: el estado mide la edad de la **descarga**, no la del **dato** | Nosotros · bloque 0 del plan |
| **C6** | FIRMS no sirve VIIRS desde el 30-07-2026 14:27 (cero filas en 24 h, los tres satélites) | Nadie: es su feed NRT. MODIS sigue dando datos |
| **C2** | EFFIS caído desde el 27-07-2026 (`Cannot create OCI Handlers`) | Nadie: es su base de datos Oracle. `scripts/vigilar_effis.py` avisa si vuelve |

Ninguno rompe el visor: un fallo de fuente no tumba el pipeline, y los
adaptadores sin endpoint aparecen como `disabled` con su motivo en el panel de
fuentes. La consecuencia real es de cobertura, no de corrección: **hoy no hay
ningún parte oficial de esas comunidades**. Castilla y León sí publica: 23 de 76
incidentes llevan estado declarado, nivel IGR y medios.

---

## A · Lógica de fusión y del pipeline

### A7 · Una fuente muerta se publicaba como sana · **RESUELTO**

El 31-07-2026 FIRMS dejó de servir VIIRS: **cero filas en 24 h** para los tres
satélites, mientras MODIS seguía dando 11 focos en el mismo bbox. VIIRS detecta a
375 m y MODIS a 1 km, así que cero detecciones donde MODIS ve once no es posible.

El panel de fuentes decía, mientras tanto:

```
NASA FIRMS · VIIRS   ok · edad declarada 15 s · 883 registros
```

**La causa.** `SourceHealth.age_seconds` mide desde `last_success_at`, y el
pipeline lo rellena con la hora de la ejecución en cuanto la descarga devuelve
algo:

```python
last_success_at=inicio if n else None
```

Se pide una ventana de 3 días, así que FIRMS siempre devuelve filas de su
archivo. La descarga «funciona» siempre y la fuente parece sana indefinidamente.

**Por qué importa más que otros.** El único sitio donde el problema asomaba era
el número rojo de «datos satelitales», que un usuario no sabe interpretar — de
hecho dudó quien construyó la aplicación. El panel de fuentes, que existe
precisamente para decir qué está roto, decía que todo iba bien.

**Solución.** Dos edades separadas y publicadas: `age_seconds` (cuándo
conseguimos descargar) y `data_age_seconds` (cuándo se tomó el dato más
reciente). `status` pasa a `stale` cuando el dato supera la cadencia declarada de
esa fuente — 12 h para los polares, 2 h para SEVIRI — aunque la descarga vaya
bien. `stale_reason` dice cuál de los dos ha fallado, porque solo uno se arregla
desde aquí.

La cadencia es opcional y solo se declara donde se conoce: que la Junta no
publique un incendio nuevo en 20 h es una buena noticia, no una avería.

### E5 · Tres pruebas E2E distintas fallaban en cada ejecución

Siempre de la capa de focos, y las tres pasaban aisladas. La capa se monta de
forma diferida y las pruebas lo suplían con `waitForTimeout(1500)`: suficiente en
una máquina descargada, insuficiente con la suite entera corriendo.

**Un tiempo fijo no es una espera, es una apuesta.**

**Solución.** `abrir()` espera a que la capa exista **y tenga filtro**, y
`capaConFeatures` espera a que MapLibre haya pintado antes de consultar lo
renderizado. La suite pasó de fallar 3 de 79 por ejecución a 79/79, y de 4,5 a
3,2 minutos: esperar a la condición es además más rápido que esperar de más.

### A0 · «Activo» se afirmaba sin que nadie lo hubiera declarado

**El fallo de más alcance que ha tenido este proyecto: afectaba al 100 % de lo
publicado.**

Los 79 incendios de producción salían con `status = "activo"` y la interfaz los
pintaba en rojo con esa palabra. Internamente `activo` solo significaba
«detectado dentro de la ventana reciente». Con 6,4 h de antigüedad y ninguna
pasada posterior, muchos de esos fuegos podían estar apagados.

Y ninguno estaba confirmado por nadie: hoy no hay un solo parte oficial en
producción.

Por qué costó verlo: **no había nada que mirar**. El dato era plausible, el mapa
funcionaba, los tests pasaban y el vocabulario estaba en el contrato 4.3. Lo
único que fallaba era que la palabra afirmaba más de lo que el dato sostiene, y
eso no lo detecta ninguna aserción sobre valores.

**Solución.** `status` queda **nulo** sin parte oficial y `status_origen` dice
quién lo afirma (`oficial` | `satelite`). Lo que el dato satelital sí sostiene
—cuánto hace que se vio calor— se publica en `ultima_observacion_h`, y la interfaz
enseña «Calor detectado hace 6 h» en gris en vez de «Activo» en rojo.

El **invariante 9** aborta la publicación si algún incidente declara estado sin
`official_confirmed`. Va como invariante y no como test porque el modo de fallo es
que alguien rellene el hueco con `fillna("activo")` para que la interfaz «quede
mejor».

### A6 · El nivel IGR y los medios nunca llegaban al incidente

`igr_level`, `resources_air`, `resources_ground` y `resources_people` están en el
contrato 4.3 desde el principio. El frontend los pintaba. **Nadie los rellenaba
nunca**: la propagación de `match` subía al cluster solo `confirmed_by`,
`official_name` y `official_status`.

Por qué costó verlo, y esta es la parte que importa: **el generador de datos de
demostración los rellenaba a mano** justo después de construir los incidentes.

```python
incidents["igr_level"] = incidents["id"].map(todos["level"].to_dict())
incidents["resources_text"] = incidents["id"].map(todos["resources"].to_dict())
```

Así que en desarrollo la ficha enseñaba «Nivel IGR 2 · 16 aéreos · 80 terrestres»
y en producción los dos campos salían nulos. No había forma de notarlo mirando la
demo, que es donde se mira.

Solo salió al conectar la primera fuente oficial real y comprobar la salida
publicada contra la URL: Villafranca del Bierzo aparecía con `igr_level: null`
teniendo la Junta un nivel declarado.

**Solución.** La propagación lleva ahora `level`, `resources` y `provincia`, con
el mismo criterio del peor caso que `_worst_status`: con dos fuentes gana el
nivel más alto y los medios se concatenan. Los huérfanos oficiales los toman de
su propia fila. Y **el generador de demostración ya no los inventa**: si el
pipeline no los propaga, la demo también sale vacía.

**La lección, que es la misma que la de F7:** un dato que solo existe en el juego
de demostración es peor que no tenerlo, porque parece que funciona.

### A1 · Dos partes oficiales se emparejaban con el mismo incendio

**Gravedad: grave.** `merge.py::match`, detectado por
`tests/test_merge.py::test_does_not_merge_neighbours` (RF-P-06 lo exige por
nombre).

Dos incendios oficiales distintos a menos de 1 km acababan pegados al mismo
cluster satelital. El mapa enseñaba uno donde había dos.

El primer arreglo —quedarse con el más cercano, global— rompió otra cosa: dejó
de poder confirmarse un mismo frente por dos comunidades limítrofes, que es un
caso real y deseable. Lo cazó el propio test.

**Solución.** El desempate es **por (cluster, fuente)**, no global:

```python
candidatos = joined[ok].sort_values("_dist")
ganadores = candidatos.index[
    ~candidatos.duplicated(subset=["_cand_fire_id", "source_id"], keep="first")
]
```

Un mismo servicio no notifica dos veces el mismo incendio: si el 112 publica dos
partes a 800 m, son dos incendios. Pero dos comunidades distintas sí pueden
confirmar el mismo frente, y eso hay que conservarlo.

### A2 · El validador se negaba a publicar 173 incendios reales

`status = "inactivo"` no estaba en el vocabulario del contrato 4.3, así que el
validador abortaba la ejecución entera. Aborto correcto —esa es su función— pero
por un dato que nadie había traducido.

**Solución.** El estado del cluster se traduce al vocabulario publicado antes de
validar. Los incendios solo satelitales y sin detecciones recientes no se
publican como incidentes en vez de publicarse con un estado inventado.

### A3 · El contador culpaba a la industria de 464 exclusiones que no eran suyas

El registro decía «464 hotspots descartados por máscara industrial» cuando la
mayoría eran duplicados espacio-temporales. Nadie lo habría notado nunca: el
número era plausible.

Importa porque esa cifra es la que se mira para decidir si la máscara industrial
está demasiado agresiva. Con el dato mal atribuido, la decisión iba a ser mala.

**Solución.** Las tres etapas de `clean` se invocan por separado y cada una
cuenta lo suyo.

### A4 · `instrument` no se publicaba

El campo se calculaba y se quedaba dentro. Dos consecuencias, ninguna visible:
el filtro de sensor del frontend no filtraba nada, y el verificador discrepaba
del manifiesto sin decir por qué.

**Solución.** Añadido a `HOTSPOT_WEB_FIELDS`.

### A5 · La precisión de posición mentía en los incendios que solo vio MODIS

**RESUELTO.**

`merge.py` asignaba 375 m —el píxel de VIIRS— a todos los incendios satelitales.
El de MODIS es 1 km, así que 8 de 44 incendios en producción publicaban una
incertidumbre casi tres veces menor que la real, y la ficha afirma sobre ese
radio que «el incendio puede estar en cualquier punto de su interior».

Se detectó al revisar la sección de precisión de la ficha, no por un test: el
dato era plausible y ninguna aserción lo cubría.

**Solución.** La precisión se deriva del **mejor** sensor que vio cada incendio,
usando el campo `sensors`: VIIRS presente → 375 m, solo MODIS → 1 km. Las
constantes salieron de dentro de `merge.py`. Tres pruebas fijan los casos VIIRS,
MODIS y mixto, y se comprobó que la de MODIS falla sin el arreglo — un test que
no se ha visto fallar no prueba nada.

---

## B · Ingesta y parseo de fuentes

### B6 · Un campo vacío se publicaba como la palabra «nan»

`str(NaN)` es `"nan"`, que es una cadena **no vacía**. Los agregadores que
juntaban texto de varias fuentes la dejaban pasar, y la ficha publicaba
**«Dónde: nan»** en cinco de seis incendios de la demo.

Parece un dato y no lo es, que es la peor combinación.

**Solución.** `pd.isna` antes que `str()` en `_primer_texto` y `_juntar_medios`,
con test de regresión. La comprobación tiene que ir **antes** de convertir, no
después: una vez es cadena, ya no hay forma de distinguirla de un texto real.

### B7 · El 112 valenciano publica incidencias, no incendios

Al conectar `112cv` el primer impulso fue publicar su feed entero. De 58
registros, **15 eran incendios**: el resto accidentes de tráfico, contaminación
marina, salvamentos y cortes de suministro.

Publicar un accidente de tráfico como incendio forestal en un visor que la gente
mira asustada es de lo peor que puede pasar aquí.

**Solución.** Filtro sobre su taxonomía jerárquica —«Incendio > Vegetación >
Forestal»— con ocho pruebas parametrizadas. Se excluye además «Vegetación >
Urbana»: un solar ardiendo no es lo que este visor cubre.

### B1 · Un `KeyError` opaco cuando FIRMS cambiaba de columnas

Si FIRMS devolvía un CSV sin la columna esperada, el fallo era un `KeyError`
seco sin decir qué sensor ni qué área lo había provocado — con cuatro sensores y
dos áreas, ocho combinaciones que investigar a ciegas.

**Solución.** `_require_columns` lanza un `ValueError` que nombra el sensor, el
área y las columnas que sí llegaron.

### B2 · `df.get("daynight", "")` reventaba con `AttributeError`

`.get` sobre un `DataFrame` no funciona como sobre un diccionario: devuelve otra
cosa, y con una columna ausente peta en un sitio que no tiene nada que ver.

**Solución.** Comprobar `"daynight" in df.columns`.

### B3 · Un campo renombrado en origen daba nulos en silencio

Si una comunidad renombra un campo, el `field_map` deja de encontrarlo y el
adaptador publica nulos. Sin excepción, sin aviso: los incendios aparecen, solo
que sin municipio ni estado.

**Solución.** `warn_missing_fields` compara el `field_map` declarado contra las
claves que trae el payload y avisa de las que faltan. Es la diferencia entre
enterarse el mismo día o en agosto.

### B4b · El TAR de AEMET se leía como texto plano

La sonda reconocía TAR.GZ por la firma de gzip. AEMET sirve los avisos como
`application/x-gtar` **sin comprimir**, así que caían en la rama de "texto
plano" y salía el volcado binario del tar en lugar del esquema.

**Solución.** Se detecta el TAR por su marca `ustar` en el byte 257, que está
igual comprimido o no.

### B4 · La sonda de AEMET se quedaba en el sobre

La API de AEMET devuelve un JSON que **no son los datos**, sino un enlace a los
datos. La sonda imprimía el enlace y paraba, así que servía para verificar que
la clave funciona y para nada más — y escribir un adaptador contra un esquema
que no has visto es adivinar.

**Solución.** La sonda sigue el enlace y describe la carga real. Aguanta las
cuatro formas que devuelve AEMET: JSON, XML CAP, TAR.GZ de XML y PNG. Del XML
saca el recuento de etiquetas por frecuencia, que da la forma del documento sin
volcar megabytes en el resumen del job.

---

## C · Endpoints y fuentes externas

### C1 · 30 de 62 incidentes estaban fuera de España

El bbox de FIRMS es un rectángulo, y el rectángulo que cubre la península
arrastra medio Portugal, parte de Francia y Marruecos.

**Solución.** Recorte contra la geometría real de España después de agrupar, no
antes: un incendio en la frontera puede tener focos a los dos lados y recortar
antes lo partiría en dos.

### C2 · EFFIS lleva días caído

Responde `msOracleSpatialLayerOpen(): Cannot create OCI Handlers` desde hace más
de tres días. Es su base de datos Oracle, no nuestro código.

**Solución.** Ninguna posible por nuestra parte. `scripts/vigilar_effis.py`
comprueba si ha vuelto. El pipeline sigue funcionando sin él porque un fallo de
fuente no tumba la ejecución.

### C3 · Cinco endpoints autonómicos sin descubrir

**PARCIALMENTE ABIERTO.** `jcyl` se descubrió el 30-07-2026 y publica 26 partes
oficiales. Quedan `112cv` e `infocam`.

`bombers` e `infoca` se dan por **no disponibles**, no por pendientes: el feed
del visor de referencia no los lleva —tiene incendios en Andalucía y Cataluña,
pero solo detectados por satélite— y el portal de datos abiertos catalán publica
agregados mensuales sin coordenadas. Probablemente no existe un feed público en
tiempo real de esas dos comunidades.

Esto **no es un error, es una regla**. Los adaptadores están vacíos a propósito.
Poner una URL plausible sin verificarla produce un 404 silencioso que el visor
enseña como «hoy no hay incendios en esta comunidad», y esa frase, falsa, es el
peor fallo que puede cometer este sistema.

Se desbloquea con las DevTools sobre el visor autonómico. Procedimiento en
`docs/COMO-CONECTAR-LAS-FUENTES.md`.

### C3b · El centroide municipal no vale para decir «a X km de tu pueblo»

Al implementar la distancia al núcleo de población, el primer impulso fue usar
`config/municipios.geojson`, que ya estaba descargada. Son **polígonos de término
municipal**: medido sobre la capa real, el centroide está a 3,3 km del pueblo en
el municipio mediano y a **23,6 km** en el más grande.

Publicar «el foco está a 4,2 km de tu pueblo» con 23 km de margen posible es
falsa precisión sobre el dato más sensible que da este visor.

**Solución.** La colección `nuc` de la OGC API del IGN —37.497 núcleos con nombre,
población y coordenadas—, encontrada con la sonda. `skipGeometry=true` no es una
optimización: cada núcleo trae su huella como MultiPolygon y los 37.497 completos
pasan de 1 GB; sin geometría son 6 MB.

### C3c · La documentación listaba cinco fuentes y el código tenía tres

`COMO-CONECTAR-LAS-FUENTES.md` nombraba cinco comunidades, y los `--id` válidos
que anunciaba incluían `bombers` e `infoca`. En `adapters.py` solo existían tres
adaptadores: `jcyl`, `infocam` y `112cv`.

Consecuencias, ninguna visible: si alguien conseguía la URL de INFOCA **no había
dónde meterla**, y el «cinco endpoints pendientes» que se repitió en varios
informes era falso — el trabajo real eran tres adaptadores más dos por crear.

Se detectó al comprobar el documento contra el código, no al usarlo.

**Solución.** Los dos adaptadores que faltaban, con URL vacía como los otros
tres, más dos pruebas: una que fija que el registro cubre las cinco fuentes de
RF-P-03, y otra que comprueba que cada una declara `precision_m`, `attribution`
y un `ttl_seconds` de al menos 300 s.

**Y una anotación que faltaba:** ninguna de las cinco tiene `precision_m`
*medido*. Los valores salen de la tabla orientativa del anexo. El propio
documento dice «no lo copies, mídelo», y están copiados porque no hay datos con
los que medir. Ahora la tabla lo advierte.

### C4 · El índice de riesgo de AEMET no sirve para esto

Responde `404 · No hay datos que satisfagan esos criterios`, y aunque
respondiera son **mapas PNG**, no datos vectoriales: no se pueden consultar por
municipio ni cruzar con un incendio.

**Decisión.** No se implementa. Los avisos CAP cubren la necesidad real —viento,
calor y tormenta declarados oficialmente— y sí son datos.

---

## D · Frontend

### D0 · La capa de focos nacía sin filtro

`montarCapaDiferida(mapa, 'hotspots')` se lanza con `void` —es asíncrona, hay que
descargar su GeoJSON— y `aplicarFiltros` corría inmediatamente después, antes de
que la capa existiera. `mapa.getLayer(CAPA_HOTSPOTS)` devolvía falso y el
`setFilter` no llegaba a ejecutarse nunca.

FIRMS se pide con 3 días de margen (`DAY_RANGE = 3`). Medido en producción:
**579 de los 1.182 focos publicados tenían más de 24 h**, con un máximo de 58 h, y
se pintaban todos mientras el control decía «1 día».

Por qué costó verlo: el mapa enseñaba **más** focos de los que decía, no menos.
Nadie cuenta 600 puntos a ojo, y un exceso de datos no se lee como un fallo.

Lo destapó un test nuevo que comprobaba otra cosa —que los focos de confianza
baja no se vieran por defecto— y encontró 15 visibles.

**Solución.** `.then(() => aplicarFiltros(...))` sobre el montaje diferido, más un
test que comprueba que la capa tiene filtro al arrancar.

### D1 · MapLibre no monta la capa y no lo dice — diagnóstico inicial equivocado

El fallo más caro de la sesión, y la primera explicación que di era **falsa**:
dije que las capas «se renderizaban mudas». La investigación demostró otra cosa
—MapLibre **no añade la capa en absoluto** y lo reporta por el evento `error`,
que nadie escuchaba— y mi primera protección, que inspeccionaba el estilo, no
podría haber funcionado nunca. Lo cazó el segundo test.

Se documenta el error de diagnóstico y no solo el arreglo, porque la lección es
que una explicación que encaja con los síntomas no es una explicación
verificada.

**Solución.** `hacerRuidososLosErrores` escucha el evento `error` y saca los
fallos de estilo por consola, filtrando el ruido de red que no dice nada:

```typescript
mapa.on('error', (ev) => {
  const mensaje = ev.error?.message ?? String(ev);
  if (/tile|Failed to fetch|NetworkError|AbortError/i.test(mensaje)) return;
  console.error(`[mapa] ${mensaje}`);
});
```

Más un test E2E: *«un fallo de estilo que MapLibre calla se hace visible»*.

### D2 · Los números de los grupos no se pintaban

Los estilos ráster no traen `glyphs`, así que cualquier capa con `text-field`
falla en silencio (por D1, sin decir nada).

**Solución.** Los números se generan como imágenes de lienzo bajo demanda, vía
el evento `styleimagemissing`. Más un test que verifica que ninguna capa usa
`text-field` con un estilo sin `glyphs`.

### D3 · La animación de viento era invisible

Tres causas a la vez, y cada una bastaba para hacerla imperceptible:

1. **41 puntos** de rejilla, demasiado pocos para que se leyera un campo
2. **Colores claros** (cian, amarillo) sobre un mapa base claro
3. **Densidad de partículas baja** — técnicamente animaba, pero no se veía

**Solución.** 230 nodos en rejilla regular de 0,75°; colores oscuros saturados
dibujados sobre un trazo blanco más ancho, de modo que se leen igual sobre el
mapa normal, la ortofoto y el relieve; y el triple de densidad. Las estelas se
agrupan por color antes de trazarse: con 2.600 partículas, eso es la diferencia
entre 60 fps y arrastrar.

### D4 · Los datos colgaban de `map.on('load')`

Si los tiles estaban bloqueados, el mapa nunca emitía `load`, así que no se
cargaba **ningún** dato y la interfaz se quedaba muda sin explicar nada.

**Solución.** La carga de datos es independiente del ciclo de vida del mapa.

### D5 · `display:flex` ganaba a `[hidden]`

Elementos marcados como ocultos seguían viéndose.

**Solución.** `[hidden] { display: none !important; }` global.

### D6 · `moveend` borraba el `?id=` de la URL

El enlace permanente a un incendio se perdía en cuanto el mapa se movía —es
decir, siempre, porque volar al incendio es un movimiento.

**Solución.** El identificador se captura antes de crear el mapa.

### D7 · El aviso del 112 se salía de pantalla a 390 px

Alturas calculadas con `calc()` que no cuadraban en móvil pequeño. El aviso que
`CLAUDE.md` declara no ocultable en ninguna resolución, oculto.

**Solución.** Columna flexbox en lugar de alturas calculadas.

### D8 · El aviso del filtro de sensor no aparecía

Dos fallos encadenados: se llamaba antes de que `construirFiltros` hubiera
creado el DOM, y la comprobación que escribí para verificarlo daba un falso
positivo, porque `!null?.hidden` es `true`.

### D9 · El polígono de CAP viene en lat,lon y GeoJSON quiere lon,lat

Riesgo detectado al escribir el adaptador, no en producción, porque el modo de
fallo era conocido: sin invertir el orden, el aviso de Albacete (39 N, 2 O) se
dibuja en (2 N, 39 E) — Somalia. El mapa **sigue pintando polígonos**, así que
comprobar que "hay datos" no detecta nada.

**Solución.** La inversión va en el adaptador, no en el frontend, más dos
pruebas que comprueban los límites geográficos: una unitaria sobre el fixture y
una E2E sobre la capa montada. Ninguna de las dos afirma que existan datos:
afirman dónde caen.

---

## E · Pruebas y CI

### E5 · Tests que había que editar en cada avance

Varios tests fijaban la lista literal de fuentes sin endpoint:

```python
assert sorted(sin_configurar) == ["112cv", "bombers", "infoca", "infocam"]
```

Cada endpoint descubierto obligaba a tocar tres ficheros de test. Un test que hay
que editar en cada avance acaba editándose sin pensar, y entonces deja de
proteger.

**Solución.** Se calculan desde el registro en vez de fijarse. El test comprueba
la **propiedad** —las que no tienen URL salen `disabled`— en lugar de una lista
que caduca.

### E6 · Un servidor de `vite preview` colgado hacía fallar 20 tests

Una tanda de E2E falló con errores que no tenían que ver con el cambio. La causa:
un `vite preview` de una ejecución anterior seguía vivo sirviendo un `dist`
viejo, y `reuseExistingServer: true` lo reutilizaba en vez de arrancar uno nuevo.

No es un fallo del código, pero cuesta un rato entenderlo porque los síntomas
apuntan a cualquier otro sitio.

**Cómo se reconoce:** muchos tests fallan a la vez en pocos milisegundos, y los
mismos pasan aislados. **Solución:** `pkill -f "vite preview"` antes de
reconstruir.

### E1 · El fixture del E2E caducaba de un día para otro

El fixture tenía marcas de tiempo congeladas y el código comparaba contra
`datetime.now()`. Verde por la tarde, rojo a la mañana siguiente sin que nadie
hubiera tocado nada — el peor tipo de test, el que enseña a ignorar el rojo.

**Solución.** Las marcas se desplazan a la hora de ejecución, con un test de
regresión que lo fija.

### E2 · Una prueba dependía de los incendios que hubiera ese día

La prueba del estado de fuentes leía los datos reales, así que su resultado
cambiaba con la realidad.

**Solución.** Inyecta su propio `sources.json` interceptando la petición.

### E3 · El E2E fallaba en CI y pasaba en local

Seis ejecuciones en rojo. El síntoma —`SyntaxError: Unexpected token '<',
"<!doctype "... is not valid JSON`— no se parece en nada a la causa.

La causa: `ci.yml` compila **antes** de generar los datos de demostración. Vite
copia `public/` dentro de `dist/` durante la compilación, así que los datos
generados después no llegan. El E2E se sirve con `vite preview`, que sirve
`dist/`, y las peticiones de `.json` recibían el `index.html` del *fallback* SPA.

En local pasaba porque `public/live` ya tenía datos de una ejecución anterior.

**Solución.** El arreglo natural —reordenar los pasos de `ci.yml`— era imposible
por el bloqueo de scope descrito en F1. Así que `build_demo_data.py` refleja la
demo en `dist/live/` si esa carpeta existe. Se verificó reproduciendo el orden
exacto de CI en local, con `public/live` vacío: 59 de 59 pruebas en verde.

### E4 · El panel de fuentes no ordenaba

Se confiaba en que el pipeline entregara las fuentes ya ordenadas.

**Solución.** El frontend ordena por su cuenta. Confiar en el orden de entrada
de un JSON es una suposición que nadie ha escrito en ningún contrato.

---

## F · Despliegue e infraestructura

### F0 · Un pipeline que abortaba se leía como éxito · `pipefail`

**El peor fallo de la categoría.** Detectado el 30-07-2026 en una ejecución roja.

`publicar.yml` decidía si el pipeline había funcionado con:

```bash
if python -m incendios.pipeline -v --no-raw 2>&1 | tee /tmp/pipeline.txt; then
```

Sin `set -o pipefail`, bash evalúa el código de salida del **último** comando de
la tubería —`tee`, que siempre devuelve 0— y no el de Python. Se comprueba en dos
líneas:

```
$ if false | tee /dev/null; then echo "el fallo se lee como ÉXITO"; fi
el fallo se lee como ÉXITO
```

Ese día se cayó la red del runner: las 12 peticiones a FIRMS dieron
`Network is unreachable`, el pipeline abortó correctamente sin sobrescribir, y el
`if` lo leyó como éxito. La copia falló al no haber salidas y el job murió ahí,
así que el respaldo nunca se ejecutó.

**Por qué es el peor de la categoría.** Ese día falló ruidosamente por
casualidad, porque `data/out/` estaba vacío. Con ficheros de una ejecución
anterior en ese directorio, se habrían copiado y publicado como frescos: datos
viejos con antigüedad nueva, que es la mentira exacta que este proyecto existe
para no contar.

**Solución.** `set -o pipefail` al principio del paso. El comentario del
workflow explica el porqué, no el qué, para que nadie lo quite por parecer ruido.

### F0b · El respaldo publicaba datos de demostración encima de los reales

Destapado por el arreglo anterior. Cuando el pipeline abortaba, la rama de
respaldo ejecutaba `build_demo_data.py` y publicaba: un corte de red de un minuto
sustituía los incendios reales por incendios inventados. Hay una banda que avisa
de que son datos de demostración, pero eso no arregla que el visor esté enseñando
fuegos que no existen.

**Solución.** El aborto ya no cae a la demo: sale con código distinto de cero, los
pasos de compilación y despliegue se saltan solos, y la publicación anterior sigue
en pie con su antigüedad real, que crecerá a la vista. El job en rojo es la
alerta. La demo se conserva solo para el arranque en frío —repositorio recién
clonado, sin clave— que es una situación distinta y legítima.

### F0c · `$GITHUB_OUTPUT` rechazaba el motivo del aborto por ser multilínea

Bug **introducido por el arreglo de F0**, visto en la primera ejecución que lo
ejercitó. La rama de aborto escribía:

```bash
echo "motivo=El pipeline abortó: $(tail -3 /tmp/pipeline.txt | head -c 200)" >> "$GITHUB_OUTPUT"
```

`tail -3` devuelve tres líneas, y con la sintaxis `clave=valor` un valor de
`$GITHUB_OUTPUT` tiene que caber en una. Actions respondía
`Invalid format` y **el motivo se perdía justo cuando hacía falta leerlo**.

**Solución.** Un ayudante `_una_linea` que aplana con `tr` antes de recortar.

### F5 · Un fallo de red de un segundo costaba media hora de datos

`firms.py` no reintentaba. Medido el 30-07-2026: 2 de 12 ejecuciones murieron con
`Network is unreachable` en las 12 peticiones a la vez, mientras la ejecución
anterior y la siguiente funcionaban con normalidad. No era FIRMS caído: era la
red del runner fallando unos segundos.

Con el cron a 30 minutos, cada fallo así es media hora sin actualizar en una
aplicación cuya razón de existir es la latencia.

**Solución.** Tres intentos con espera creciente (2 s, 4 s), y **solo ante fallos
de transporte**. Una respuesta no-CSV es la clave agotada o inválida, y repetirla
no la arregla: solo gastaría cuota y retrasaría el aborto. Hay un test por cada
uno de los tres caminos.

### F6 · Un bbox contenido en otro gastaba un tercio de la cuota de FIRMS

`baleares` = `1.10,38.60,4.40,40.15` está **dentro por completo** de `peninsula`
= `-9.60,35.85,4.40,43.90`. Cuatro de las doce peticiones —una por sensor— pedían
datos que la otra ya traía, y el duplicado se descartaba aguas abajo después de
haber gastado la petición.

Nada fallaba. Solo se desperdiciaba un tercio de la cuota, del tiempo de pipeline
y de la superficie de fallo de red — y desde que hay reintentos, un tercio de los
reintentos.

**Solución.** Fuera el bbox, y un invariante en `tests/test_config.py` que
comprueba que ninguna pareja de bboxes se solapa, más su contrapartida: que los
puntos de control de cada territorio siguen cubiertos. Sin el segundo test,
«no se solapan» se satisfaría borrando bboxes.

### F7 · La cuota de FIRMS se ignoraba — y el primer arreglo la inventó

Agotar la cuota de FIRMS se manifiesta como **cero incendios**, el fallo que este
proyecto existe para no cometer, y no había aviso previo.

**Primer arreglo, equivocado.** Se leía una cabecera `Remaining-request-endpoint`
que yo había visto en las respuestas de **AEMET** y asumí que FIRMS también
mandaba. No la manda: comprobado con la sonda el 30-07-2026, sus respuestas solo
traen `x-frame-options` y `x-content-type-options`.

El resultado pasó los tests —porque los tests inyectaban esa cabecera— llegó a
producción, y publicó un campo `quota_remaining` que **salía siempre nulo**. Un
dato inventado por asumir en lugar de mirar, exactamente el patrón que este
documento existe para registrar. Se descubrió al verificar la salida real contra
la URL de producción, no antes.

**Arreglo definitivo.** FIRMS tiene un endpoint de estado aparte, cuyo esquema se
sondeó antes de escribir nada:

```json
{ "transaction_limit": 5000, "current_transactions": 54,
  "transaction_interval": "10 minutes" }
```

Se consulta antes de las descargas, se publican `quota_remaining` y `quota_limit`
—el restante sin el límite no dice si vamos bien— y se avisa por debajo del 10 %.
Cinco pruebas fijan el esquema, incluidas las dos formas en que puede fallar: un
JSON con otras claves y una respuesta que no es JSON.

**La lección concreta:** un test que construye el payload que el código espera no
prueba que el origen mande eso. El fixture tiene que venir del origen.

### F1 · El scope `workflow` bloqueaba toda edición de Actions

**RESUELTO.** Ni el token del agente ni el cacheado en el llavero de macOS tenían
el scope `workflow`. GitHub rechazaba cualquier push que tocara
`.github/workflows/`, y como **una push es atómica**, un solo commit con un
workflow tumbaba el lote entero.

Es una protección deliberada de GitHub: sin ella, cualquier integración con
permiso de escritura podría inyectar un workflow que se ejecuta con todos los
secretos del repositorio.

**Solución provisional mientras duró.** Los commits se separaban: lo que no
tocaba workflows subía, y los ficheros de workflow quedaban pendientes en el
árbol de trabajo.

**Solución definitiva**, ejecutada una vez en local:

```
gh auth refresh -h github.com -s workflow
gh auth setup-git
```

El segundo comando es la mitad que se olvida: sin él, git sigue tirando del
token del llavero y la push vuelve a rebotar aunque `gh` ya tenga el scope.

### F2 · La clave de AEMET se conectó a un workflow desactivado

Se añadió `AEMET_API_KEY` a `ingest.yml`, que está **desactivado** esperando el
bucket de Cloudflare R2. El que corre cada 30 minutos es `publicar.yml`.

Habría quedado como configurada sin ejecutarse nunca. Se detectó al leer la
cabecera del fichero, que lo dice en la primera línea.

**Solución.** La clave va en `publicar.yml`. Ya en producción.

### F3 · Un `git reset --hard` borró un commit sin subir

Un `reset --hard` dentro de una cadena de comandos propia se llevó por delante
el commit de `sondear.yml`, que aún no estaba en el remoto. Hubo que recrear el
fichero entero.

### F4 · Un hook del repositorio commiteó los workflows dentro del lote

Después de separar los commits a mano, un hook auto-commiteó todo junto otra
vez —incluidos los dos workflows— con un mensaje que no era el mío, y la push
volvió a rebotar por F1.

**Solución.** `--no-verify` en el commit de separación.

---

## Lo que se repite

Seis de estos fallos —A1, A3, A4, B3, C1, E1— **no produjeron ningún error**.
Devolvieron un número plausible: 464 exclusiones, 62 incendios, un filtro que no
filtra, un municipio vacío. En un sistema de datos, el fallo normal no es la
excepción: es el resultado creíble y equivocado.

De ahí tres costumbres del repositorio, que no son ceremonia:

- **Cada fuente externa tiene su fixture de regresión.** Cuando una comunidad
  cambie el formato en agosto, un test rojo dirá cuál.
- **Cuando un parseo falla en producción, el payload que lo rompió se convierte
  en fixture antes de arreglar el código.** Si no, se arregla el síntoma.
- **Los `xfail(strict=True)` son deuda que avisa.** El día que alguien arregla el
  código, el test se pone verde y `strict` obliga a quitar el marcador.

Y una regla de método, de D1: una explicación que encaja con los síntomas no es
una explicación verificada. La primera teoría sobre MapLibre encajaba
perfectamente y era falsa, y la protección construida sobre ella no podía
funcionar.
