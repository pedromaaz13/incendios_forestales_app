# Qué hay en esta carpeta

Cuatro documentos con cuatro propósitos distintos. Si dudas de cuál abrir, la
pregunta que te estás haciendo decide:

| Tu pregunta | Documento |
|---|---|
| ¿Qué tiene que hacer esto y cómo se comprueba? | **ESPECIFICACION.md** |
| ¿Qué funciona hoy y qué toca ahora? | **ESTADO-DEL-PROYECTO.md** |
| ¿Por qué está escrito así? ¿Qué se rompió ya? | **ERRORES-Y-SOLUCIONES.md** |
| ¿Qué tengo que hacer yo para que haya datos reales? | **COMO-CONECTAR-LAS-FUENTES.md** |

## En detalle

**ESPECIFICACION.md** · el contrato. Requisitos numerados (`RF-P-*` pipeline,
`RF-F-*` frontend, `RNF-*` no funcionales), pruebas exigidas y criterios de
aceptación. Es la única fuente de verdad sobre lo que el sistema *debe* hacer;
lo demás describe lo que *hace*. No se edita para justificar el código: si el
código no lo cumple, el código está mal.

**ESTADO-DEL-PROYECTO.md** · dónde estamos. Estado por módulo, recuentos reales
de la última ejecución, y el plan ordenado por lo que más rinde. Todo lo que
afirma está comprobado contra el repositorio o contra producción en su fecha de
cabecera. Es el documento que se lee para retomar el trabajo.

**ERRORES-Y-SOLUCIONES.md** · qué se rompió y por qué costó verlo. Empieza con
la tabla de lo que sigue abierto. Se escribe porque el patrón se repite: casi
ningún fallo de este proyecto dio un error — devolvieron un número plausible y
equivocado, que es la forma que tiene de fallar un sistema de datos. La columna
que más importa de cada entrada no es la solución, es *por qué no saltó nadie*.

**COMO-CONECTAR-LAS-FUENTES.md** · la tarea que no puede hacer un agente. De las
cinco fuentes autonómicas hay **dos conectadas** —Castilla y León y el 112
valenciano— y las demás siguen con la URL vacía **a propósito**: una URL
inventada devuelve 404 en silencio y eso se lee como «hoy no hay incendios».
Sacarlas exige abrir el visor de cada comunidad con las DevTools. Lleva un anexo
técnico para cuando ya tengas una URL y toque escribir el adaptador, y un
registro de lo ya comprobado — incluido lo que **no** sirve, que ahorra repetir
trabajo descartado.

## Cómo está montado el pipeline

Por si hace falta situar un módulo sin leerlos todos. El orden es el de
ejecución:

| Módulo | Qué hace |
|---|---|
| `firms.py` | Baja los focos de calor de NASA FIRMS (VIIRS ×3 + MODIS) |
| `clean.py` | Confianza, máscara industrial y dedup entre pasadas |
| `cluster.py` | Agrupa focos en incendios (ST-DBSCAN) y traza perímetros |
| `enrich.py` | Recorta a España y pone municipio y provincia |
| `sources/` | Partes oficiales de los servicios autonómicos |
| `merge.py` | **Fusiona** oficial ↔ satélite. El módulo menos obvio del repo |
| `contexto.py` | Cruza viento, avisos, cortes, ritmo y distancia a población |
| `suelo.py` | Etiqueta el terreno: monte, cultivo o urbano |
| `validate.py` | Los 9 invariantes. Aborta la publicación si alguno falla |
| `build.py` | Manifiesto con las dos latencias y los recuentos |
| `health.py` | Estado por fuente: qué funciona y qué no, y por qué |
| `export.py` · `publish.py` | GeoJSON y publicación atómica |

Las tres reglas que explican casi todas las decisiones raras del código:

1. **Un fallo de fuente no tumba el pipeline**, pero **sí se publica que falló**.
2. **Nada se afirma sin quien lo afirme.** Sin parte oficial no hay estado.
3. **Ante la duda, no se muestra.** Un hueco explícito es honesto; un cero
   silencioso se lee como «hoy no arde nada».

## `evidencias/`

Capturas de pantalla generadas por `web/tests/e2e/capturas.spec.ts`, no a mano.
La sección 9 de la especificación exige que un requisito sin captura no se
considere entregado.

Se regeneran con:

```bash
cd web && npx playwright test tests/e2e/capturas.spec.ts
```

Al estar versionadas, un cambio visual no intencionado aparece en el diff de git.
El coste es que son binarios y que algunas se reescriben en cada ejecución porque
el panel de latencia muestra la hora.

## Fuera de esta carpeta

- **`AGENTS.md`** (raíz) · contrato de ingeniería y reglas duras. Vale para
  cualquier agente y se lee primero.
- **`CLAUDE.md`** (raíz) · punto de entrada de Claude Code: importa `AGENTS.md`
  y añade el contexto del dominio y el orden de lectura.
- **`.ai/`** (raíz) · plantillas de tarea, fallo, traspaso y ADR. Ver
  `.ai/README.md`.
- **`README.md`** (raíz) · arquitectura y decisiones tomadas, con su porqué.
- **`PROMPTS.md`** (raíz) · andamio de método, no documentación del producto:
  prompts por hito para abrir sesiones de trabajo.
