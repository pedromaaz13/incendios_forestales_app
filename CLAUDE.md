@AGENTS.md

# Contexto del proyecto

Visor público de incendios forestales activos en España. Combina detecciones
satelitales (NASA FIRMS, EUMETSAT) con partes oficiales de los servicios
autonómicos de extinción.

## Aviso de dominio

Esto lo mira gente asustada buscando si arde algo cerca de su casa. El lenguaje
de la interfaz usa «estimación» y «detección», nunca verbos de certeza. El aviso
de que no sustituye al **112** es permanente y no se oculta en ninguna
resolución.

## Orden de lectura

Abre solo el que responda a tu pregunta. No los leas todos.

| Tu pregunta | Fichero |
|---|---|
| ¿Qué debe hacer y cómo se comprueba? | `docs/ESPECIFICACION.md` |
| ¿Qué funciona hoy y qué toca ahora? | `docs/ESTADO-DEL-PROYECTO.md` |
| ¿Por qué está escrito así? ¿Qué se rompió ya? | `docs/ERRORES-Y-SOLUCIONES.md` |
| ¿Qué falta para tener datos reales? | `docs/COMO-CONECTAR-LAS-FUENTES.md` |
| ¿Qué parámetro toco? | `src/incendios/config.py` |
| Voy a tocar la fusión oficial ↔ satélite | `src/incendios/merge.py`, entero |

El estado por módulo vive en `docs/ESTADO-DEL-PROYECTO.md` y solo ahí: una
segunda copia aquí se queda desfasada sin que nadie lo note.

## Notas para Claude Code

- `Read`, `Glob` y `Grep` antes que salida ancha de shell.
- `/compact` cuando la tarea sigue siendo válida pero el hilo se ha ensuciado.
  Sesión nueva cuando cambia el objetivo.
- No actives servidores MCP ajenos a la tarea.
- Respuestas por debajo de 250 palabras salvo que la tarea pida documentación.
- Plantillas de tarea, handoff, debug y ADR en `.ai/` — ver `.ai/README.md`.
