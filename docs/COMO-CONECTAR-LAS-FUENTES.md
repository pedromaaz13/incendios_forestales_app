# Cómo conectar las fuentes de datos

Guía práctica. Si haces solo esto, la aplicación pasa de datos de demostración a
datos reales.

---

## Por qué hace falta que lo hagas tú

El agente que ha escrito el código **no tiene salida a internet**: el entorno
donde corre bloquea todo lo que no sea npm y PyPI. Puede buscar en la web y leer
resúmenes, pero no puede abrir el visor de la Junta de Andalucía ni comprobar si
una URL responde.

Y no vale con que ponga una URL plausible. La primera regla dura de `CLAUDE.md`
existe justamente por esto:

> Una URL falsa devuelve 404 en silencio y eso se lee como "hoy no hay
> incendios" — el fallo más peligroso de este sistema.

Un endpoint sin verificar no es un atajo: es un mapa que dice "0 incendios en
Andalucía" un día de agosto. Por eso las cinco fuentes salen hoy como `disabled`
en el panel de estado, y no como "0 incendios".

Tu ordenador sí tiene internet. `scripts/descubrir_fuentes.py` hace el trabajo
técnico; tú solo tienes que copiar una URL y pegarme lo que salga.

---

## Paso 1 · La clave de NASA FIRMS (5 minutos)

Es lo que desbloquea los datos satelitales, que son la mitad del producto.

1. Entra en <https://firms.modaps.eosdis.nasa.gov/api/map_key/>
2. Pon tu correo. Te llega la clave al momento. Es gratis y sin condiciones.
3. Compruébala **antes** de meterla en ningún sitio:

```bash
export FIRMS_MAP_KEY=la-clave-que-te-han-dado
python scripts/descubrir_fuentes.py --firms
```

Si sale `✓ Clave válida · N hotspots`, funciona. Entonces la metes en el
repositorio: **Settings → Secrets and variables → Actions → New repository
secret**, con el nombre exacto `FIRMS_MAP_KEY`.

Con esto solo, el cron ya empieza a acumular histórico cada 10 minutos. La
especificación insiste en que es urgente: cada día sin correr es histórico
irrecuperable, y la máscara de falsos positivos lo necesita para construirse.

---

## Paso 2 · Un endpoint autonómico (15 minutos el primero)

Los cinco funcionan igual. Empieza por **uno solo**; cuando ese esté, los demás
son repetir el procedimiento.

### Encontrar la URL

1. Abre el **visor oficial** de la comunidad. El mapa, no la nota de prensa:

   | Fuente | Qué buscar |
   |---|---|
   | JCyL | "INFORCYL" o el visor de incendios de la Junta de Castilla y León |
   | Bombers | Visor d'incendis forestals de la Generalitat de Catalunya |
   | INFOCA | Visor de incendios activos de la Junta de Andalucía |
   | INFOCAM | Sistema FIDIAS de Castilla-La Mancha |
   | 112 CV | Portal de emergencias de la Generalitat Valenciana |

2. Abre las herramientas de desarrollador: **F12** (o **⌘⌥I** en Mac).
3. Pestaña **Network** (o **Red**).
4. Filtro **Fetch/XHR**. Esto quita imágenes y CSS y deja solo datos.
5. Recarga a lo bruto: **Ctrl+Shift+R** (**⌘⇧R** en Mac).
6. Mira la lista. Busca la petición cuyo **tamaño crezca con el número de
   incendios del día**. Suele llamarse `query`, `features`, `incendios`,
   `getFeature` o algo parecido.
7. Clic derecho sobre ella → **Copy → Copy link address**.

### Comprobarla

```bash
export INCENDIOS_CONTACTO=tu@correo.es   # va en el User-Agent, es obligatorio
python scripts/descubrir_fuentes.py --url "LA-URL-QUE-HAS-COPIADO" --id jcyl
```

El script te dirá:

- si responde, y con qué;
- **los nombres reales de los campos**, que es lo que de verdad hace falta;
- un `field_map` propuesto, adivinado por el nombre de cada campo;
- y guardará la respuesta en `tests/fixtures/jcyl.json`.

### Pasármelo

Copia la salida completa del script y pégamela. Con eso configuro el adaptador,
escribo el test de parseo contra el fixture y el test de que un HTTP 500
devuelve un DataFrame vacío sin lanzar. Los `--id` válidos son `jcyl`,
`bombers`, `infoca`, `infocam` y `112cv`.

---

## Si el visor no usa una API

Puede pasar que la web pinte el mapa desde HTML, sin ninguna petición de datos.
Alternativas, por orden de preferencia:

1. **Busca su GeoServer o su ArcGIS.** Muchas comunidades publican en su IDE
   (infraestructura de datos espaciales):

   ```bash
   python scripts/descubrir_fuentes.py --listar "https://el-servidor/ArcGIS/rest/services"
   ```

   Con GeoServer, un WMS casi siempre tiene un WFS al lado que devuelve JSON:
   cambia `/wms` por `/wfs` y añade `?service=WFS&request=GetCapabilities` para
   ver qué capas hay. Castilla y León, por ejemplo, tiene un GeoServer en
   `idecyl.jcyl.es` — no lo he podido verificar desde aquí, pero es el primer
   sitio donde miraría.

2. **Portal de datos abiertos** de la comunidad, o <https://datos.gob.es>.

3. **Escríbeles.** Suena lento y suele ser lo más rápido: los servicios de
   emergencias tienen interés en que su información llegue. Un correo diciendo
   qué estás haciendo y pidiendo acceso a los datos abiertos funciona más de lo
   que parece, y de paso evita que te bloqueen por tráfico que no reconocen.

---

## Reglas que no se saltan

- **User-Agent con tu correo.** Si un administrador ve tráfico que no entiende,
  que pueda escribirte en vez de bloquearte.
- **TTL nunca por debajo de 300 s.** Pedir cada 15 minutos está bien; cada 30
  segundos es abusar de un servicio público.
- **`precision_m` se mide, no se copia.** Gobierna toda la fusión y el tamaño
  del anillo de incertidumbre del mapa. Procedimiento en «Medir `precision_m`»,
  más abajo.
- **Atribución visible.** Ya está en el panel lateral; mantenla.

---

## Y entonces qué

Con un endpoint configurado y la clave de FIRMS puesta:

- el cron corre cada 10 minutos y publica en R2;
- el frontend deja de mostrar la banda azul de demostración;
- esa comunidad pasa de `disabled` a `ok` en el panel de estado;
- sus partes empiezan a fusionarse con las detecciones satelitales, que es
  donde está el valor: un incendio confirmado, localizado con precisión y con
  intensidad medida.

Las otras cuatro comunidades son el mismo procedimiento, y cada una que añadas
es una fila verde más en el panel.

---

# Anexo técnico

Lo de aquí abajo hace falta cuando ya tienes una URL que responde y toca escribir
el adaptador. Antes, no.

## Reconocer el tipo de backend

| Lo que ves en la URL | Adaptador | Truco |
|---|---|---|
| `/FeatureServer/0/query` o `/MapServer/0/query` | `ArcGISSource` | Pide `?f=pjson` sobre la capa: devuelve el esquema completo con los nombres reales de campo |
| `/api/...` devolviendo JSON propio | `JsonApiSource` | Escribe el `extract` a mano |
| WMS/WFS (`service=WFS`) | `JsonApiSource` con `outputFormat=application/json` | Prueba `request=GetCapabilities` para listar capas |
| Solo HTML | Último recurso: `selectolax` o `lxml` | Frágil. Añade un test que falle si cambia el DOM |

## Medir `precision_m` (no lo copies, mídelo)

Es el parámetro que gobierna toda la fusión. Procedimiento:

1. Coge un incendio del portal oficial con nombre de paraje concreto.
2. Localiza ese paraje en el visor del PNOA o en OSM.
3. Mide la distancia entre la coordenada publicada y la posición real.
4. Repite con 5 incendios. Usa el percentil 90, no la media.

Valores esperables como punto de partida. **Son los que hay puestos hoy en
`adapters.py`, y ninguno está medido**: se copiaron de esta tabla a falta de
datos con los que medir. En cuanto una fuente devuelva incendios reales, el
primer trabajo es sustituir su valor por uno medido.

| Fuente | Precisión típica | Motivo |
|---|---|---|
| 112 Comunitat Valenciana | ~100 m | Coordenadas del incidente |
| JCyL | ~500 m | Posición del incendio, no del municipio |
| Bombers · INFOCA | ~1–2 km | Varía según cómo se cargue el parte |
| INFOCAM / FIDIAS | ~6 km | Centroide del municipio, declarado por la fuente |

## Test de regresión

Para cada fuente configurada, guarda una respuesta real en
`tests/fixtures/{source_id}.json` y añade un test que valide el parseo contra
ella. Es lo que convierte cinco scrapers frágiles en cinco scrapers mantenibles.

---

## Registro de lo verificado · 28/07/2026

Comprobado con peticiones reales, no por búsqueda. Se anota tanto lo que sirve
como lo que **no**, porque descartar una vía también ahorra trabajo.

### El visor de referencia no expone los endpoints

`incendiosespaña.es` no llama a ninguna fuente autonómica desde el navegador.
Su `app.js` solo pide a su **propio backend**:

```
/api/fires  /api/wind  /api/effis-perimeters  /api/jcyl-extra
/api/source-status  /api/road-closures  /api/hydrants  /api/aprs
```

Las URL autonómicas viven en su servidor, que no es público (`/api/source-status`
responde `{"error": "Acceso no autorizado"}`). **Inspeccionar su pestaña Network
no sirve de nada**: solo se ve `/api/fires`.

Y consumir su API tampoco es una opción: haría el proyecto dependiente de la
infraestructura de un tercero y sería aprovecharse de su trabajo. La sección 12.3
ya lo excluye.

### Fuentes probadas

| Fuente | Resultado |
|---|---|
| `idecyl.jcyl.es/geoserver/wfs` | ✅ Responde. Solo capas de **riesgo y prevención** (`plainc26_cyl_riesgo_*`, `areas_peligro_if`, `puntos_inicio_2015_2024`). Ningún incendio activo |
| `analisi.transparenciacatalunya.cat` | ✅ Responde. `jq8m-d7cw` (incidentes CAT112) son **agregados mensuales** por municipio y tipo, sin coordenadas ni hora. No sirve para tiempo real |
| `ies-ows.jrc.ec.europa.eu/effis` (WFS) | ⚠️ Anuncia las capas buenas —`ercc.ba` áreas quemadas, `fwi_nuts5.fwi` índice de riesgo, `ercc.hs_24hrs_point`— pero **su backend Oracle está caído**: `msOracleSpatialLayerOpen(): Connection failure`. Reintentar |
| `maps.effis.emergency.copernicus.eu` | ❌ No resuelve |
| `www.ign.es/wfs-inspire/unidades-administrativas` | Sustituido: la capa municipal se prepara desde la descarga GML del CNIG |
| `image.discomap.eea.europa.eu/.../Natura2000/N2K_2018` | ❌ **Descartada por cobertura incompleta.** Ver abajo |
| `wms.mapama.gob.es/sig/Biodiversidad/RedNatura` | ❌ Su servidor devuelve `System.NullReferenceException` tanto en WMS como en WFS. Comprobado el 05-08-2026 |

### Natura 2000: por qué se descartó la capa de la EEA · 05/08/2026

El servicio existe, es público, soporta `Query` por coordenada y responde
rápido — igual que CORINE. Parecía directo. **No lo es.**

Sondeados trece puntos de espacios que sí son Red Natura 2000:

| Tipo de espacio | Dentro |
|---|---:|
| Monte y bosque — Picos de Europa, Gredos, Monfragüe, Sierra Nevada, Cazorla, Ordesa, Doñana | **7 / 7** |
| Humedales — Delta del Ebro (3 puntos), Tablas de Daimiel, Albufera de Valencia | **0 / 5** |

`N2K_2018` no es la capa de límites de los espacios: es el **mapa de hábitats
terrestres** dentro de algunos de ellos. Los humedales no están.

Usarla habría publicado «no está en espacio protegido» de un incendio en la
Albufera. Un falso negativo silencioso, del tipo exacto que este proyecto existe
para no cometer: no da error, no lo nota nadie, y afirma lo contrario de la
realidad.

**Qué haría falta** para hacer esta capa bien: los límites oficiales de la Red
Natura 2000 con su nombre y código de espacio. Los publica MITECO, cuyo servidor
está roto, y la EEA en su portal de descargas —no como API consultable—, así que
tocaría el patrón de `preparar_nucleos.py`: descargar una vez, recortar a España
y servir estático.

### Lo que esto significa

Las cinco fuentes autonómicas de RF-P-03 **no tienen un endpoint público
documentado**. Cada comunidad publica en su visor y los datos van por peticiones
internas de esa página concreta. El procedimiento de DevTools de arriba sigue
siendo la vía, pero hay que hacerlo **en el visor de cada comunidad**, no en un
agregador.

**EFFIS es la mejor apuesta cuando vuelva.** Es europeo, documentado, estable y
aporta dos cosas que ninguna autonómica da:

- `ercc.ba` → perímetros de área quemada **medidos**, que sustituirían a las
  envolventes estimadas de `cluster.build_perimeters`.
- `fwi_nuts5.fwi` → el índice de riesgo meteorológico **oficial** por municipio.
  Resuelve el reparo de calcular el FWI por nuestra cuenta sin poder validarlo:
  aquí lo publica quien lo define.
