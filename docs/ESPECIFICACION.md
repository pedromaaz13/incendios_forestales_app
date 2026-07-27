# Especificación técnica · Visor de incendios forestales en España

**Versión:** 1.0
**Fecha:** 27 de julio de 2026
**Destinatario:** equipo de desarrollo o agente de codificación autónomo
**Repositorio base:** `incendios-es` (pipeline FIRMS ya implementado y probado)

---

## 0. Cómo usar este documento

Es una especificación ejecutable. Cada requisito tiene identificador (`RF-P-01`,
`RF-F-07`, `RNF-03`) y criterio de aceptación verificable. **No se acepta un
requisito sin su prueba automatizada y su evidencia visual** según la sección 9.

Si eres un agente de codificación:

1. Lee las secciones 1–4 completas antes de escribir una línea.
2. Implementa por hitos (sección 10). No avances de hito sin que el anterior
   pase todas sus pruebas.
3. Cuando un requisito sea ambiguo o dependa de un dato que no tienes
   (endpoints oficiales, claves), **para y pregunta**. No inventes URLs ni
   valores. Un endpoint falso que devuelve 404 se interpreta como "hoy no hay
   incendios" y ese es el fallo más peligroso de este sistema.
4. Cada PR incluye: código, pruebas, capturas y una nota de qué decidiste tú.

---

## 1. Objetivo y alcance

### 1.1 Qué construimos

Un visor web público de incendios forestales activos en España que combina:

- **Detecciones satelitales** (NASA FIRMS VIIRS/MODIS, EUMETSAT LSA-SAF SEVIRI)
  agrupadas en incendios reales, no mostradas como puntos crudos.
- **Confirmaciones oficiales** de los servicios autonómicos de extinción.
- **Contexto operativo**: viento, perímetros de área quemada, cortes de carretera.

### 1.2 Qué NO construimos

Delimitar esto es tan importante como lo anterior.

| Fuera de alcance | Motivo |
|---|---|
| Predicción de propagación | Requiere modelo físico, combustible y validación. Otro proyecto |
| Capas de radioafición (APRS, LoRa, Meshtastic) | No aportan información sobre incendios |
| App móvil nativa | La web responsive cubre el caso de uso |
| Cuentas de usuario | Solo suscripción por email a una zona (hito 5), sin login |
| Sustituir al 112 | Se declara explícitamente en la interfaz |

### 1.3 Principio rector

> **Un hotspot no es un incendio, y la latencia se publica siempre.**

Toda decisión de diseño en conflicto se resuelve a favor de no inducir a error.
Es preferible mostrar "sin datos" que mostrar datos viejos como si fueran
actuales.

---

## 2. Stack

### 2.1 Obligatorio

| Capa | Tecnología | Motivo |
|---|---|---|
| Pipeline | Python 3.12 | Ya implementado. `pandas`, `geopandas`, `scikit-learn`, `httpx` |
| Orquestación | GitHub Actions (cron) | Coste cero, sin servidor que mantener |
| Almacenamiento | Cloudflare R2 (S3-compatible) | Sin egress fee |
| Formato web | GeoJSON + PMTiles | PMTiles a partir de 15.000 features |
| Formato analítico | Parquet particionado por `acq_date` | Consultable con DuckDB |
| Mapa | MapLibre GL JS 4.x | Ver 2.3 |
| Frontend | Vite + TypeScript, sin framework | Ver 2.3 |
| Hosting web | Cloudflare Pages | Estático, CDN incluido |
| Pruebas Python | `pytest` + `pytest-cov` | — |
| Pruebas E2E | Playwright | Genera las capturas exigidas |
| Formato/lint | `ruff` (Python), `biome` (TS) | — |

### 2.2 Prohibido

- **Base de datos en el camino de lectura.** El frontend lee ficheros estáticos
  de CDN. Si crees que necesitas una BD, para y justifícalo.
- **Cualquier framework de componentes** (React, Vue, Svelte). El frontend es un
  mapa con paneles. Añadir un framework aquí es 40 KB de runtime para nada.
- **`localStorage` para datos de incendios.** Solo para preferencias de UI.
- **Extraer, descompilar o reutilizar código de terceros.** La ingeniería inversa
  de arquitectura es legítima; copiar bundles no. Todo el código es original.

### 2.3 Justificación de MapLibre frente a Leaflet

La referencia del sector usa Leaflet + markercluster. Elegimos MapLibre por
razones concretas, no por modernidad:

1. **Filtros en GPU.** Filtrar por confianza, sensor y ventana temporal con
   `setFilter` es instantáneo sobre 50.000 features. Con Leaflet hay que
   reconstruir la capa de marcadores en JS.
2. **PMTiles nativo.** Escala a histórico completo sin cambiar de arquitectura.
3. **Interpolaciones por zoom** en `paint`, sin recalcular en JS.
4. **Rotación e inclinación** para la vista de progresión temporal (hito 6).

Contrapartida asumida: WebGL obligatorio. Se implementa una detección y un
mensaje explícito de navegador no soportado (`RNF-08`).

---

## 3. Arquitectura

```
┌─────────────── GitHub Actions · cron */10 ────────────────────────┐
│                                                                   │
│  ┌── ingesta paralela (aislada por fuente) ──┐                    │
│  │  FIRMS VIIRS×3 + MODIS   (4 sensores)     │                    │
│  │  LSA-SAF SEVIRI FRP-PIXEL                 │                    │
│  │  JCyL · Bombers · INFOCA · INFOCAM · 112CV│                    │
│  │  EFFIS perímetros                         │                    │
│  │  Open-Meteo viento (35 puntos)            │                    │
│  └───────────────────┬───────────────────────┘                    │
│                      ▼                                            │
│  clean      filtro confianza → máscara industrial → dedup         │
│  cluster    ST-DBSCAN → fire_id estable → concave hull            │
│  merge      fusión oficial↔satélite con tolerancia por fuente     │
│  enrich     geocoding inverso (IGN local, sin Nominatim)          │
│  export     GeoJSON · PMTiles · Parquet · manifest · páginas SEO  │
│  health     estado por fuente → sources.json                      │
└───────────────────────────┬───────────────────────────────────────┘
                            │ aws s3 sync
                            ▼
        Cloudflare R2 ──── CDN ────► Cloudflare Pages (Vite build)
        live/     max-age=120, stale-while-revalidate=600
        history/  max-age=31536000, immutable
        seo/      max-age=3600
```

### 3.1 Contrato de artefactos publicados

Todo bajo `live/`:

| Fichero | Contenido | Tamaño objetivo |
|---|---|---|
| `manifest.json` | Metadatos de ejecución, latencias, totales | < 4 KB |
| `sources.json` | Estado de salud por fuente | < 8 KB |
| `incidents.geojson` | Incidentes unificados (satélite + oficial) | < 400 KB |
| `perimeters.geojson` | Perímetros EFFIS + estimados | < 1,5 MB |
| `hotspots.geojson` | Hotspots crudos, últimas 24 h | < 3 MB |
| `hotspots.pmtiles` | Hotspots, ventana de 3 días | < 12 MB |
| `wind.geojson` | 35 puntos de viento | < 20 KB |

**Regla de atomicidad:** el `sync` publica primero los datos y `manifest.json`
al final. El frontend nunca debe leer un manifiesto que apunte a datos que aún
no están.

---

## 4. Modelo de datos

### 4.1 `manifest.json`

```jsonc
{
  "schema_version": 1,
  "generated_at": "2026-07-27T18:00:12Z",

  // DOS latencias distintas. Confundirlas es el error de diseño que
  // corregimos respecto a la competencia.
  "pipeline_age_seconds": 42,        // desde la última ejecución
  "data_age_seconds": {
    "firms_viirs": 8340,             // ~2,3 h: última pasada polar
    "seviri": 900,                   // 15 min: geoestacionario
    "official": 300                  // 5 min: scrapers
  },
  "worst_data_age_seconds": 8340,

  "counts": {
    "incidents_total": 61,
    "incidents_satellite_confirmed": 44,
    "incidents_official_only": 17,
    "hotspots_24h": 2007,
    "hotspots_suppressed_industrial": 38,
    "hotspots_suppressed_lowconf": 412
  },

  "frp_total_mw": 14820.5,
  "degraded": false,                 // true si alguna fuente crítica falla
  "degraded_reason": null,

  "disclaimer": "Detecciones satelitales de anomalías térmicas..."
}
```

### 4.2 `sources.json`

```jsonc
{
  "generated_at": "2026-07-27T18:00:12Z",
  "sources": [
    {
      "id": "jcyl",
      "name": "Junta de Castilla y León",
      "region": "Castilla y León",
      "kind": "oficial",              // oficial | satelite | contexto
      "critical": true,               // si falla, degraded = true
      "status": "ok",                 // ok | stale | error | disabled
      "last_success_at": "2026-07-27T17:55:03Z",
      "age_seconds": 309,
      "ttl_seconds": 300,
      "records": 12,
      "precision_m": 500,
      "error": null,
      "consecutive_failures": 0
    }
  ]
}
```

### 4.3 `incidents.geojson` · propiedades por feature

| Campo | Tipo | Obligatorio | Descripción |
|---|---|---|---|
| `id` | string | sí | Estable entre ejecuciones |
| `origin` | enum | sí | `satelite` \| `oficial` \| `ambos` |
| `satellite_confirmed` | bool | sí | Hay hotspots asociados |
| `official_confirmed` | bool | sí | Hay parte oficial asociado |
| `confirmed_by` | string | sí | Lista separada por comas de `source_id` |
| `status` | enum | sí | `activo` \| `estabilizado` \| `controlado` \| `extinguido` |
| `municipio` | string | no | Del IGN si hay satélite, del parte si no |
| `provincia` | string | no | — |
| `igr_level` | int | no | Nivel IGR, solo fuentes que lo publican |
| `resources_air` | int | no | Medios aéreos |
| `resources_ground` | int | no | Medios terrestres |
| `resources_people` | int | no | Personas |
| `n_hotspots` | int | no | 0 si es solo oficial |
| `frp_total_mw` | float | no | — |
| `intensity` | enum | no | `baja` \| `media` \| `alta` \| `extrema` |
| `area_est_ha` | float | no | **Siempre etiquetado como estimación** |
| `position_precision_m` | float | sí | Gobierna el radio de incertidumbre dibujado |
| `first_detected` | ISO8601 | sí | — |
| `last_detected` | ISO8601 | sí | — |
| `started_at` | ISO8601 | no | Inicio oficial, si lo hay |

### 4.4 Invariantes que deben cumplirse siempre

Se validan en `RF-P-14` y se prueban en `test_invariants.py`:

1. `id` es único en el fichero.
2. `satellite_confirmed OR official_confirmed` es verdadero. No hay incidentes sin origen.
3. `origin == "ambos"` ⟺ ambos flags verdaderos.
4. `first_detected <= last_detected`.
5. Toda geometría cae dentro del bbox de España (incluidas Canarias).
6. `position_precision_m > 0`.
7. Si `n_hotspots == 0` entonces `origin == "oficial"`.
8. `status == "extinguido"` no aparece en `incidents.geojson` (se filtra).

---

## 5. Requisitos funcionales · pipeline

### RF-P-01 · Ingesta FIRMS
**Ya implementado.** Verificar que sigue cumpliendo: 4 sensores × 3 bboxes en
paralelo, normalización de esquema VIIRS/MODIS, detección de respuesta no-CSV
(FIRMS devuelve HTTP 200 con texto de error cuando la clave está agotada).

**Prueba:** `test_firms.py::test_detects_non_csv_response` con un fixture de
respuesta de cuota agotada. Debe devolver DataFrame vacío, no lanzar.

---

### RF-P-02 · Ingesta SEVIRI (LSA-SAF)
Integrar el producto **FRP-PIXEL** de EUMETSAT LSA-SAF sobre Meteosat MSG.

- Cadencia: 15 min. Resolución ~3 km en la latitud ibérica.
- Formato: netCDF o texto según el canal de distribución elegido.
- Requiere registro en LSA-SAF.
- Los hotspots SEVIRI se marcan con `instrument = "SEVIRI"` y entran en el mismo
  clustering, pero **con `precision_m = 3000`**, no 375.

**Por qué importa:** VIIRS deja huecos de horas entre pasadas. SEVIRI cubre esa
ventana de forma continua. Es la mejora de latencia con mayor retorno.

**Criterio de aceptación:** un incendio detectado por SEVIRI a las 14:15 aparece
en el visor antes de las 14:35.

**Prueba:** `test_seviri.py` con fixture netCDF real reducido (< 200 KB).

---

### RF-P-03 · Adaptadores de fuentes oficiales
**Framework ya implementado** en `src/incendios/sources/`. Falta configurar cinco
fuentes siguiendo `docs/descubrimiento-fuentes.md`:

| `source_id` | Comunidad | `precision_m` estimada | Notas |
|---|---|---|---|
| `jcyl` | Castilla y León | 500 | Publica IGR y desglose de medios. La más rica |
| `bombers` | Cataluña | 1500 | — |
| `infoca` | Andalucía | 1500 | — |
| `infocam` | Castilla-La Mancha | 6000 | **Centroide municipal.** Declarado por la fuente |
| `112cv` | Comunitat Valenciana | 100 | Coordenadas del incidente |

`precision_m` **debe medirse**, no copiarse: 5 incendios con paraje conocido,
comparar contra PNOA, tomar percentil 90.

**Requisitos duros:**
- User-Agent identificable con correo de contacto.
- Respetar `ttl_seconds`. Nunca por debajo de 300 s.
- Una fuente caída **no puede** tumbar el pipeline (ya cubierto por el `try` de
  `OfficialSource.collect`; no eliminarlo).
- Guardar el payload crudo en `data/raw/{source_id}/` cuando el parseo falle.
- Atribución visible en el frontend.

**Prueba:** por cada fuente, un fixture real en `tests/fixtures/{source_id}.json`
y un test que valide el parseo contra él. Más un test que inyecte un HTTP 500 y
verifique que `collect()` devuelve DataFrame vacío sin lanzar.

---

### RF-P-04 · Máscara de falsos positivos
Semilla manual en `config/exclusions.geojson` (ya existe, coordenadas **por
verificar contra PNOA**). Buffer configurable, por defecto 1200 m.

`scripts/build_exclusions.py` amplía la máscara desde el histórico: celdas de
~450 m con detección en ≥ 45 días distintos.

**Requisito nuevo:** las candidatas automáticas **no se aplican solas**. Se
escriben en `exclusions_auto.geojson` y requieren revisión humana antes de
fusionarse. Un falso positivo en la máscara oculta incendios reales.

**Prueba:** `test_clean.py::test_suppresses_refinery` — hotspots sintéticos sobre
Puertollano deben desaparecer; hotspots a 5 km deben sobrevivir.

---

### RF-P-05 · Clustering
**Ya implementado.** ST-DBSCAN por proyección del eje temporal.

**Ajuste requerido:** `eps_m` debe escalar con la resolución del sensor. Un
hotspot SEVIRI de 3 km no puede usar el mismo `eps` que uno VIIRS de 375 m.
Implementar `eps` efectivo por punto o clustering en dos pasadas.

**Prueba:** `test_cluster.py::test_seviri_does_not_overmerge` — dos incendios
VIIRS separados 4 km con un hotspot SEVIRI entre ambos no deben fusionarse en uno.

---

### RF-P-06 · Fusión oficial ↔ satélite
**Ya implementado y probado** en `src/incendios/merge.py`.

Reglas confirmadas por la prueba existente:
- Tolerancia = `precision_m + 3000 m`, tope 15 km.
- Ventana temporal 48 h.
- Ante estados discrepantes, gana el más grave.
- Los oficiales sin satélite se conservan como huérfanos.

**Prueba adicional requerida:** `test_merge.py::test_does_not_merge_neighbours` —
dos incendios oficiales de 112 CV a 800 m uno de otro con un solo cluster FIRMS
cerca: solo uno debe emparejarse (el más próximo), no ambos.

---

### RF-P-07 · Geocoding inverso
Spatial join contra recintos municipales del IGN cargados localmente.

**No usar Nominatim.** Rate limit, dependencia externa en el camino crítico y
latencia. El IGN son 30 MB de GeoJSON que se cargan una vez.

**Prueba:** 20 coordenadas conocidas con su municipio esperado.

---

### RF-P-08 · Perímetros
Dos fuentes, dibujadas distinto:

1. **EFFIS** (Copernicus): perímetros cartografiados. Fiables. Trazo sólido.
2. **Estimados** desde el concave hull de los hotspots. Trazo punteado, y la
   etiqueta debe decir "estimación", no "área quemada".

**Prueba:** validar que ningún perímetro estimado se exporta sin
`is_estimate: true`.

---

### RF-P-09 · Viento
Open-Meteo, 35 puntos distribuidos. Velocidad, dirección, ráfagas. TTL 15 min.

**Requisito de diseño:** las flechas apuntan **hacia donde sopla** el viento, no
de donde viene. La convención meteorológica es la contraria y confunde a
cualquiera que no sea meteorólogo. Documentarlo en la leyenda.

---

### RF-P-10 · Estado de fuentes (`sources.json`)
Por cada fuente registrada, emitir el bloque de 4.2. Estados:

- `ok` — última ejecución con éxito dentro del TTL.
- `stale` — éxito, pero `age_seconds > ttl_seconds * 3`.
- `error` — último intento falló.
- `disabled` — sin configurar (endpoint vacío).

`degraded = true` si alguna fuente con `critical: true` está en `error` o `stale`,
o si `worst_data_age_seconds > 14400` (4 h).

**Prueba:** `test_health.py` con fuentes simuladas en cada estado.

---

### RF-P-11 · Publicación atómica
Orden estricto: datos → `sources.json` → `manifest.json`. Si el pipeline falla a
mitad, `manifest.json` no se actualiza y el frontend sigue mostrando el estado
anterior con su edad real, que crecerá visiblemente.

**Nunca publicar salidas vacías.** Si FIRMS devuelve 0 hotspots y el histórico
reciente tenía cientos, es un fallo de la fuente, no ausencia de incendios:
abortar sin sobrescribir.

**Prueba:** `test_export.py::test_aborts_on_suspicious_emptiness`.

---

### RF-P-12 · Histórico
Parquet particionado por `acq_date`, append idempotente. Ya implementado.

Añadir: partición también por `instrument` para que las consultas por sensor no
lean todo.

---

### RF-P-13 · Páginas SEO por municipio
Generar HTML estático en `seo/incendios/{provincia}/{municipio}/index.html` para
municipios con incidentes activos o en los últimos 7 días.

Contenido mínimo: nombre, estado, fuentes que lo confirman, momento de la última
detección, mapa estático, enlace al visor centrado, aviso de que no es
información oficial de emergencias.

**Cabecera obligatoria:** `<meta name="robots" content="index">` solo si hay
incidente. Sin incidente, la página no se genera — no publicar 8.100 páginas
vacías, es *doorway spam* y Google penaliza.

**Prueba:** generar para un municipio con y sin incidente; verificar que la
segunda no existe.

---

### RF-P-14 · Validación de invariantes
Antes de publicar, validar los 8 invariantes de 4.4. Si alguno falla, abortar la
publicación y salir con código distinto de cero para que Actions marque fallo.

**Prueba:** `test_invariants.py` con un fichero deliberadamente corrupto por cada
invariante (8 casos).

---

## 6. Requisitos funcionales · frontend

### RF-F-01 · Mapa base
MapLibre GL. Estilo por defecto claro (mejor legibilidad diurna, que es cuando
se consulta). Selector de estilo: `Normal` / `Satélite` / `Relieve`.

Controles: zoom con nivel visible, pantalla completa, geolocalización.

Persistencia de estilo en `localStorage`.

---

### RF-F-02 · Estado en la URL
`?lat=&lon=&zoom=` sincronizado con `moveend` vía `history.replaceState`.
Añadir `&id=` para enlace profundo a un incidente concreto: al cargar con `id`,
centrar y abrir su ficha.

**Prueba E2E:** cargar `?id=X`, verificar que la ficha está abierta y el mapa
centrado en la geometría correcta.

---

### RF-F-03 · Capa de incidentes
Un símbolo por incidente, no por hotspot. Codificación:

| Propiedad visual | Variable | Motivo |
|---|---|---|
| Radio | `n_hotspots` (interpolado por zoom) | Tamaño aparente del incendio |
| Color de relleno | `intensity` (rampa de brasa) | Derivada del FRP |
| Grosor de borde | `official_confirmed` | Confirmado = borde grueso |
| Opacidad | `status` | Activo opaco, controlado translúcido |
| Anillo punteado | `position_precision_m` | **Radio de incertidumbre real** |

El anillo de incertidumbre es la diferencia clave frente a la competencia: un
incidente de INFOCAM con ±6 km debe dibujarse con un anillo de 6 km, no con un
punto que finge precisión que no tiene.

---

### RF-F-04 · Capa de hotspots
Solo visible a partir de zoom 9. Puntos pequeños, semitransparentes.

**Prohibido el clustering numérico a zoom bajo.** Un badge con "638" no informa
de nada: mezcla incendios reales, quemas agrícolas y falsos positivos en un
número que parece una magnitud. A zoom bajo se muestran los incidentes; los
hotspots son detalle de zoom alto.

---

### RF-F-05 · Panel de latencia
Elemento principal de la cabecera. Debe mostrar **dos números distintos**:

```
DATOS SATELITALES          ACTUALIZACIÓN
    2 h 19 min                  hace 4 min
  última pasada VIIRS       refresco cada 10 min
```

Umbrales de color: verde < 1 h · ámbar 1–4 h · rojo > 4 h.

Si `degraded == true`, banda de aviso persistente con el motivo.

**Prueba E2E:** manipular `manifest.json` para forzar cada umbral y capturar.

---

### RF-F-06 · Panel de estado de fuentes
Desplegable en la barra lateral. Una fila por fuente: nombre, indicador de estado
por color, antigüedad, número de registros. Fuentes en `error` arriba.

Accesible: el estado no puede transmitirse solo por color. Icono + texto.

---

### RF-F-07 · Lista de incidentes visibles
Barra lateral derecha, sincronizada con el viewport (`moveend`). Ordenada por
gravedad: activos primero, luego por FRP descendente.

Cada tarjeta: distintivo de comunidad, municipio y provincia, estado, nivel IGR
si lo hay, medios desglosados, momento de inicio, quién lo confirma.

Al pasar el ratón, resaltar en el mapa. Al pulsar, centrar y abrir ficha.

Virtualizada si supera 100 elementos.

---

### RF-F-08 · Buscador
Búsqueda de municipio contra un índice estático generado en el pipeline
(nombre, provincia, coordenadas, ~8.100 entradas, < 400 KB comprimido).

Sin llamadas externas. Navegable con teclado, `aria-activedescendant` correcto.

---

### RF-F-09 · Filtros
- **Período:** 1 / 2 / 3 días.
- **Confianza mínima:** todas / ≥ media / solo alta.
- **Sensor:** VIIRS · MODIS · SEVIRI (conmutables).
- **Fuente oficial:** una por comunidad.
- **Origen:** todos / solo confirmados oficialmente / solo satelitales.

Todos vía `setFilter` de MapLibre. Ninguno debe provocar recarga de datos.

**Prueba de rendimiento:** cambio de filtro sobre 50.000 features en < 100 ms.

---

### RF-F-10 · Ficha de incidente
Al pulsar un incidente. Contenido por bloques:

1. **Cabecera:** municipio, provincia, estado, quién lo confirma.
2. **Oficial** (si existe): nivel IGR, medios aéreos/terrestres/personas, inicio.
3. **Satelital** (si existe): detecciones, FRP acumulado, primera y última,
   superficie estimada **con la palabra "estimada" visible**.
4. **Precisión:** "posición con margen de ±N km según la fuente".
5. **Enlace permanente.**

---

### RF-F-11 · Capas opcionales
Conmutables, todas apagadas por defecto: perímetros, viento, cortes DGT.

Cada una carga su GeoJSON **solo al activarse**. No penalizar la carga inicial.

---

### RF-F-12 · Aviso legal permanente
Visible sin desplazar, en todas las resoluciones:

> Detecciones satelitales de anomalías térmicas y partes oficiales. No es
> información oficial de emergencias. Ante una emergencia, 112.

---

### RF-F-13 · Estado degradado y vacío
- `manifest.json` inaccesible → banda roja "No se han podido cargar los datos",
  mapa navegable, sin números inventados.
- `degraded: true` → banda ámbar con las fuentes afectadas.
- Viewport sin incidentes → la lista dice "Sin incendios en esta zona", no queda
  en blanco.

**Prueba E2E:** simular los tres estados interceptando la red con Playwright.

---

## 7. Requisitos no funcionales

| ID | Requisito | Umbral | Verificación |
|---|---|---|---|
| RNF-01 | Carga inicial (LCP), 4G simulada | < 2,5 s | Lighthouse CI |
| RNF-02 | Peso total de la carga inicial | < 900 KB | Presupuesto en CI |
| RNF-03 | Interacción con el mapa | 60 fps con 20.000 features | Perfilado manual + captura |
| RNF-04 | Cambio de filtro | < 100 ms | Prueba de rendimiento |
| RNF-05 | Accesibilidad | WCAG 2.1 AA | `axe-core` en Playwright, 0 violaciones críticas |
| RNF-06 | Navegación completa por teclado | Todo alcanzable, foco visible | Prueba E2E |
| RNF-07 | Responsive | 360 px – 2560 px | Capturas en 3 anchos |
| RNF-08 | Sin WebGL | Mensaje explícito, no pantalla en blanco | Prueba con WebGL desactivado |
| RNF-09 | `prefers-reduced-motion` | Sin animaciones | Prueba E2E |
| RNF-10 | Cobertura de pruebas del pipeline | ≥ 85 % líneas | `pytest-cov` en CI |
| RNF-11 | Duración del pipeline | < 5 min | Registro en Actions |
| RNF-12 | Contraste de la rampa de intensidad | ≥ 3:1 contra el mapa base | Comprobación manual documentada |

---

## 8. Estrategia de pruebas

### 8.1 Unitarias (`pytest`)

Un fichero por módulo. Cobertura mínima 85 %.

Casos obligatorios más allá del camino feliz:

| Caso | Módulo | Comportamiento esperado |
|---|---|---|
| FIRMS devuelve HTML de error con HTTP 200 | `firms` | DataFrame vacío, log de aviso |
| FIRMS devuelve CSV con columna faltante | `firms` | Excepción clara, no `KeyError` opaco |
| `acq_time` = `45` (sin ceros) | `firms` | Se interpreta 00:45 |
| Fuente oficial devuelve 500 | `sources` | DataFrame vacío, no lanza |
| Fuente oficial cambia el nombre de un campo | `sources` | Aviso explícito, no fila silenciosa con nulos |
| Coordenadas fuera de España | `sources` | Descartadas con aviso |
| Estado desconocido en un parte | `sources` | `desconocido`, `raw_status` conservado |
| Hotspot sobre refinería | `clean` | Suprimido |
| Hotspot a 5 km de refinería | `clean` | Conservado |
| Confianza baja | `clean` | Suprimido |
| Dos incendios a 4 km con SEVIRI entre medias | `cluster` | No se fusionan |
| Incendio de un solo hotspot | `cluster` | Genera incidente válido |
| Oficial ±6 km | `merge` | Empareja con tolerancia amplia |
| Dos oficiales a 800 m, un cluster | `merge` | Solo empareja el más próximo |
| Estados discrepantes | `merge` | Gana el más grave |
| Oficial sin satélite | `merge` | Se conserva como huérfano |
| Salida sospechosamente vacía | `export` | Aborta, no publica |
| Cada invariante violado | `export` | 8 pruebas, todas abortan |

### 8.2 Fixtures de regresión

**Obligatorio para cada fuente externa.** Guardar una respuesta real (anonimizada
si procede) en `tests/fixtures/` y probar el parseo contra ella.

Es lo que convierte cinco scrapers frágiles en cinco scrapers mantenibles: cuando
una comunidad cambie el formato en agosto, el test rojo te dice cuál y el fixture
guardado te dice qué cambió.

Regla: **cuando un parseo falle en producción, el payload que lo rompió se
convierte en un fixture nuevo** antes de arreglar el código.

### 8.3 Integración

`test_pipeline_e2e.py`: ejecuta el pipeline completo con todas las fuentes
simuladas por fixture, sin red. Verifica que los artefactos se generan, validan y
cumplen los invariantes.

Debe correr en < 30 s para que se pueda ejecutar en cada commit.

### 8.4 E2E y visual (Playwright)

Escenarios obligatorios:

| ID | Escenario | Verifica |
|---|---|---|
| E2E-01 | Carga inicial, España completa | RF-F-01, RF-F-05 |
| E2E-02 | Zoom a un incendio, abrir ficha | RF-F-03, RF-F-10 |
| E2E-03 | Filtrar solo confirmados oficialmente | RF-F-09 |
| E2E-04 | Buscar "Burgohondo" y navegar con teclado | RF-F-08, RNF-06 |
| E2E-05 | Activar viento y perímetros | RF-F-11 |
| E2E-06 | Cargar con `?id=` | RF-F-02 |
| E2E-07 | `manifest.json` devuelve 500 | RF-F-13 |
| E2E-08 | `degraded: true` | RF-F-13 |
| E2E-09 | Viewport sin incendios | RF-F-13 |
| E2E-10 | Latencia > 4 h | RF-F-05 (umbral rojo) |
| E2E-11 | WebGL desactivado | RNF-08 |
| E2E-12 | `prefers-reduced-motion` | RNF-09 |
| E2E-13 | `axe-core` en la carga inicial y con ficha abierta | RNF-05 |

Todos con datos de fixture, nunca contra producción: las pruebas deben ser
deterministas.

### 8.5 CI

```yaml
# Pull request
ruff check · ruff format --check · biome ci
pytest --cov --cov-fail-under=85
playwright test
lighthouse-ci (presupuesto de RNF-01, RNF-02)

# Rama principal
+ despliegue a entorno de pruebas
+ publicación de capturas como artefacto
```

---

## 9. Evidencias exigidas

Cada PR de un hito adjunta las capturas correspondientes. **Un requisito sin
captura no se considera entregado.**

### 9.1 Generación

Playwright, automatizadas, a `docs/evidencias/{hito}/`:

```ts
// Tres anchos obligatorios para todo escenario visual
const VIEWPORTS = [
  { name: 'movil',      width: 390,  height: 844  },
  { name: 'tablet',     width: 834,  height: 1112 },
  { name: 'escritorio', width: 1680, height: 1050 },
];
```

Datos de fixture congelados en `tests/fixtures/manifest_demo.json` para que las
capturas sean reproducibles y comparables entre PRs.

### 9.2 Listado obligatorio

| Fichero | Contenido | Requisito |
|---|---|---|
| `01-inicial-{viewport}.png` | Vista de España, todas las capas por defecto | RF-F-01 |
| `02-latencia-verde.png` | Cabecera con latencia < 1 h | RF-F-05 |
| `03-latencia-ambar.png` | Latencia 1–4 h | RF-F-05 |
| `04-latencia-roja.png` | Latencia > 4 h | RF-F-05 |
| `05-degradado.png` | Banda de aviso con fuentes caídas | RF-F-13 |
| `06-fuentes-panel.png` | Panel de estado desplegado, con una fuente en error | RF-F-06 |
| `07-incidente-ambos.png` | Ficha de incidente confirmado por satélite y oficial | RF-F-10 |
| `08-incidente-oficial.png` | Ficha de huérfano oficial, con anillo de incertidumbre | RF-F-03, RF-F-10 |
| `09-incertidumbre-infocam.png` | Anillo de ±6 km sobre un incidente de INFOCAM | RF-F-03 |
| `10-hotspots-zoom.png` | Zoom 11 con capa de hotspots visible | RF-F-04 |
| `11-filtros-oficial.png` | Filtro "solo confirmados oficialmente" aplicado | RF-F-09 |
| `12-buscador-teclado.png` | Buscador con foco visible en un resultado | RF-F-08, RNF-06 |
| `13-viento.png` | Capa de viento activa sobre un incendio | RF-F-11 |
| `14-perimetros.png` | EFFIS sólido y estimado punteado, distinguibles | RF-P-08, RF-F-11 |
| `15-vacio.png` | Viewport sin incendios, lista con mensaje | RF-F-13 |
| `16-error-red.png` | `manifest.json` caído | RF-F-13 |
| `17-sin-webgl.png` | Mensaje de navegador no soportado | RNF-08 |
| `18-axe.png` | Informe de `axe-core` con 0 violaciones críticas | RNF-05 |
| `19-lighthouse.png` | Informe con RNF-01 y RNF-02 en verde | RNF-01, RNF-02 |
| `20-pipeline-log.png` | Registro de una ejecución completa con contadores | RF-P-10, RNF-11 |

### 9.3 Regresión visual

A partir del hito 3, comparación con la captura anterior. Diferencia superior al
0,5 % de píxeles requiere justificación en el PR.

---

## 10. Hitos y criterios de aceptación

### Hito 0 · Base (hecho)
Pipeline FIRMS, clustering, exclusiones, fusión, exportación, prueba de humo.

---

### Hito 1 · Producción mínima
**Objetivo:** que el cron corra a diario y acumule histórico. Es urgente: cada
día sin correr es histórico irrecuperable, y la máscara de falsos positivos lo
necesita.

- [ ] `FIRMS_MAP_KEY` en secrets, R2 configurado
- [ ] Cron cada 10 min funcionando 72 h sin intervención
- [ ] `sources.json` y `manifest.json` publicándose (RF-P-10, RF-P-11)
- [ ] Validación de invariantes (RF-P-14)
- [ ] Alerta si `worst_data_age_seconds > 14400`

**Aceptación:** 72 h de ejecución continua, ≥ 95 % de ejecuciones con éxito,
captura `20-pipeline-log.png`.

---

### Hito 2 · Frontend v1
- [ ] RF-F-01, 02, 03, 04, 05, 10, 12, 13
- [ ] RNF-01, 02, 05, 06, 07, 08, 09
- [ ] E2E-01, 02, 06, 07, 09, 10, 11, 12, 13

**Aceptación:** capturas 01–10, 15–19. Lighthouse en verde.

---

### Hito 3 · Fuentes oficiales
- [ ] Las cinco de RF-P-03 configuradas y con fixture
- [ ] `precision_m` medido y documentado, con la metodología
- [ ] RF-F-06, 07, 09
- [ ] E2E-03, 04

**Aceptación:** capturas 06, 08, 09, 11, 12. Prueba de que una fuente caída no
degrada el resto (simular 500 en cada una, verificar que el pipeline completa).

---

### Hito 4 · Latencia y contexto
- [ ] RF-P-02 (SEVIRI), RF-P-05 ajustado, RF-P-08, RF-P-09
- [ ] RF-F-11
- [ ] E2E-05

**Aceptación:** capturas 13, 14. Demostrar con un caso real que SEVIRI detectó
un incendio antes que la siguiente pasada VIIRS.

---

### Hito 5 · Alcance
- [ ] RF-P-13 (SEO por municipio)
- [ ] Suscripción por zona: radio configurable, email al detectarse actividad
- [ ] Sitemap y datos estructurados

**Aceptación:** 20 páginas de municipio indexables, ninguna vacía. Prueba de
entrega de una alerta con datos simulados.

---

### Hito 6 · Progresión temporal
- [ ] Reproducción de la evolución de un incendio desde el histórico Parquet
- [ ] Selector de fecha y control de reproducción

**Aceptación:** reproducción de un incendio real de la temporada, capturas de
tres momentos.

---

## 11. Riesgos

| Riesgo | Impacto | Mitigación |
|---|---|---|
| Una comunidad cambia el formato | Fuente perdida sin aviso | Fixtures de regresión + alerta por `consecutive_failures` |
| FIRMS agota cuota en pico de temporada | Sin datos satelitales | Detectar respuesta no-CSV, degradar explícitamente, no publicar vacío |
| Máscara de exclusión oculta un incendio real | **Grave** | Revisión humana obligatoria; registrar siempre lo suprimido en `manifest` |
| Bloqueo por parte de un portal autonómico | Fuente perdida | UA identificable, TTL respetado, contacto previo |
| Pico de tráfico en un incendio mediático | Caída | Arquitectura estática en CDN: absorbe sin intervención |
| Interpretación del visor como fuente oficial | **Grave** | Aviso permanente, lenguaje de estimación, sin verbos de certeza |

---

## 12. Anexos

### 12.1 Glosario

| Término | Definición |
|---|---|
| **Hotspot** | Píxel con anomalía térmica detectada por satélite. No es un incendio |
| **FRP** | *Fire Radiative Power*, potencia radiativa en MW. Proxy de intensidad |
| **NRT / URT** | *Near / Ultra Real Time*. Latencia de ~3 h y ~1 h en FIRMS |
| **IGR** | Índice de Gravedad Potencial. Escala 0–2 de los servicios de extinción |
| **EFFIS** | *European Forest Fire Information System*, programa Copernicus |
| **LSA-SAF** | Centro de EUMETSAT que distribuye el producto FRP de SEVIRI |
| **PMTiles** | Formato de teselas en un solo fichero, servido por rangos HTTP |
| **ST-DBSCAN** | DBSCAN con dimensión temporal |

### 12.2 Fuentes y licencias

| Fuente | Licencia | Atribución requerida |
|---|---|---|
| NASA FIRMS | Dominio público | Sí, solicitada |
| EUMETSAT LSA-SAF | Registro + licencia de uso | Sí |
| Copernicus EFFIS | Licencia abierta | Sí |
| Portales autonómicos | Variable, revisar cada uno | Sí, obligatoria |
| Open-Meteo | CC BY 4.0 | Sí |
| IGN | CC BY 4.0 | Sí |
| OpenStreetMap | ODbL | Sí |

### 12.3 Sobre el trabajo de terceros

Existe al menos un visor público equivalente en producción. Su arquitectura ha
servido de referencia y varias de sus decisiones de producto —el panel de estado
de fuentes, la lista sincronizada con el viewport, la integración de partes
autonómicos— son buenas y las adoptamos.

**No se reutiliza su código.** Estudiar la arquitectura de un sistema en
producción es práctica de ingeniería normal; extraer y republicar su
implementación no lo es. Todo el código de este proyecto es original.

Cuando el proyecto se publique, se reconocerá el trabajo previo en el propio
sitio.
