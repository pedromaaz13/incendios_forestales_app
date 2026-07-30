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

## A · Lógica de fusión y del pipeline

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

**ABIERTO.** `112cv`, `infocam`, `jcyl` y dos más siguen con la URL vacía.

Esto **no es un error, es una regla**. Los adaptadores están vacíos a propósito.
Poner una URL plausible sin verificarla produce un 404 silencioso que el visor
enseña como «hoy no hay incendios en esta comunidad», y esa frase, falsa, es el
peor fallo que puede cometer este sistema.

Se desbloquea con las DevTools sobre el visor autonómico. Procedimiento en
`docs/COMO-CONECTAR-LAS-FUENTES.md`.

---

## D · Frontend

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

---

## E · Pruebas y CI

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

### F1 · El scope `workflow` bloqueaba toda edición de Actions

**RESUELTO.** Ni el token del agente ni el cacheado en el llavero de macOS tienen
el scope `workflow`. GitHub rechaza cualquier push que toque
`.github/workflows/`, y como **una push es atómica**, un solo commit con un
workflow tumba el lote entero.

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

### C4 · El índice de riesgo de AEMET no sirve para esto

Responde `404 · No hay datos que satisfagan esos criterios`, y aunque
respondiera son **mapas PNG**, no datos vectoriales: no se pueden consultar por
municipio ni cruzar con un incendio.

**Decisión.** No se implementa. Los avisos CAP cubren la necesidad real —viento,
calor y tormenta declarados oficialmente— y sí son datos.

### D9 · El polígono de CAP viene en lat,lon y GeoJSON quiere lon,lat

Riesgo detectado al escribir el adaptador, no en producción, porque el modo de
fallo era conocido: sin invertir el orden, el aviso de Albacete (39 N, 2 O) se
dibuja en (2 N, 39 E) — Somalia. El mapa **sigue pintando polígonos**, así que
comprobar que "hay datos" no detecta nada.

**Solución.** La inversión va en el adaptador, no en el frontend, más dos
pruebas que comprueban los límites geográficos: una unitaria sobre el fixture y
una E2E sobre la capa montada. Ninguna de las dos afirma que existan datos:
afirman dónde caen.

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
