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
- **`precision_m` se mide, no se copia.** Coge 5 incendios con paraje conocido,
  compara la coordenada publicada con la posición real en el PNOA y usa el
  percentil 90. Ese número gobierna toda la fusión y el tamaño del anillo de
  incertidumbre del mapa.
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
