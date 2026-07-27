# Descubrimiento de endpoints oficiales

Los adaptadores de `src/incendios/sources/adapters.py` están sin URL a propósito.
Este documento es el procedimiento para rellenarlos.

## Por qué no vienen rellenos

Estos endpoints no están documentados públicamente, cambian sin aviso y varios
son internos del visor de cada comunidad. Poner una URL inventada produce un
`404` silencioso que parece "hoy no hay incendios" — el peor fallo posible en
este dominio. Un hueco explícito es honesto.

## Procedimiento por fuente

1. Abre el **visor oficial** de la comunidad (no la nota de prensa: el mapa).
2. DevTools → Network → filtro **Fetch/XHR** → recarga forzada.
3. Busca la petición cuyo tamaño crezca con el número de incendios del día.
   Suele llamarse `query`, `features`, `incendios` o similar.
4. Copia la URL completa. Pega en el adaptador.

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

Valores esperables como punto de partida:

| Fuente | Precisión típica | Motivo |
|---|---|---|
| 112 Comunitat Valenciana | ~100 m | Coordenadas del incidente |
| JCyL | ~500 m | Posición del incendio, no del municipio |
| Bombers · INFOCA | ~1–2 km | Varía según cómo se cargue el parte |
| INFOCAM / FIDIAS | ~6 km | Centroide del municipio, declarado por la fuente |

## Higiene obligatoria

- **User-Agent identificable con contacto.** `incendios-es/1.0 (+correo)`. Si un
  administrador ve tráfico raro, que pueda escribirte en vez de bloquearte.
- **Respeta el TTL.** `ttl_seconds` en `SourceMeta` existe para eso. Pedir cada
  15 min a un portal autonómico está bien; cada 30 s no.
- **Un fallo no tumba nada.** `OfficialSource.collect` ya aísla excepciones. No
  quites ese `try`.
- **Atribución visible en el mapa.** Es lo mínimo, y es lo que hace que nadie
  te corte el acceso.
- **Guarda la respuesta cruda cuando el parseo falle.** Cuando una comunidad
  cambie el formato en agosto, el payload guardado es lo único que te permitirá
  arreglarlo en 10 minutos en vez de en una tarde.

## Test de regresión

Para cada fuente configurada, guarda una respuesta real en
`tests/fixtures/{source_id}.json` y añade un test que valide el parseo contra
ella. Es lo que convierte cinco scrapers frágiles en cinco scrapers mantenibles.
