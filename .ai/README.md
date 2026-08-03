# Espacio de trabajo de los agentes

Escribir el encargo antes de empezar es lo que evita la sesión que explora el
repo entero para acabar tocando dos líneas. Cuatro plantillas, cada una para un
momento distinto:

| Cuándo | Plantilla | Dónde va la copia |
|---|---|---|
| Antes de empezar algo no trivial | `tasks/TASK.template.md` | `tasks/` |
| Algo falla y no sabes por qué | `debugging/DEBUG.template.md` | `debugging/` |
| Se acaba el contexto y la tarea no | `handoffs/HANDOFF.template.md` | `handoffs/` |
| Una decisión que alguien preguntará dentro de un año | `decisions/ADR.template.md` | `decisions/` |

Copia la plantilla con un nombre propio (`tasks/mtg-ingesta.md`), no la edites
en sitio.

## Qué se versiona y qué no

`decisions/` **sí**: un ADR es memoria del proyecto y sobrevive a la sesión.

`tasks/`, `debugging/` y `handoffs/` **no** — son estado temporal, y el
`.gitignore` los excluye salvo las plantillas. Si algo de ahí resulta que
importaba, su sitio es `docs/`:

- una causa raíz y por qué costó verla → `docs/ERRORES-Y-SOLUCIONES.md`
- qué funciona y qué toca ahora → `docs/ESTADO-DEL-PROYECTO.md`
- un requisito nuevo → `docs/ESPECIFICACION.md`

## Por qué existe

Un handoff bien escrito ahorra la relectura completa del repositorio al retomar.
La sección **«No repetir»** es la que más rinde: sin ella, cada sesión nueva
vuelve a investigar lo que la anterior ya descartó. En este proyecto eso ya ha
pasado —Sentinel-3 se investigó tres veces— y por eso la plantilla la incluye.
