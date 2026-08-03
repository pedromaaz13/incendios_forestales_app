# Contrato de ingeniería

Vale para cualquier agente que trabaje en este repositorio. El objetivo es el
cambio mínimo correcto **con evidencia**, no la máxima actividad.

## Reglas duras

Las cinco existen porque cada una previno un fallo real. No se suavizan.

**No inventes endpoints.** Los adaptadores de `src/incendios/sources/adapters.py`
tienen la URL vacía a propósito. Una URL falsa devuelve 404 en silencio y eso se
lee como «hoy no hay incendios» — el fallo más peligroso de este sistema. Si
necesitas un endpoint, pídelo.

**No publiques salidas vacías.** Si una fuente devuelve cero registros y el
histórico reciente tenía cientos, es un fallo de la fuente, no ausencia de
incendios. Abortar sin sobrescribir.

**Un fallo de fuente no tumba el pipeline.** El `try` de `OfficialSource.collect`
está ahí por eso. No lo quites.

**La latencia se publica siempre.** Son dos números distintos —edad del dato
satelital y edad de la última ejecución— y se muestran los dos. Mezclarlos
induce a error y es el fallo que este proyecto existe para no cometer.

**Nada se afirma sin quien lo afirme.** Sin parte oficial no hay estado. Ante la
duda entre mostrar un dato incierto o no mostrarlo, no se muestra.

## Antes de editar

- **Busca antes de leer.** `Grep`/`Glob` para localizar el punto de entrada. No
  leas ficheros enteros si te basta un símbolo, ni recorras el repo por defecto.
- **Una sesión, una tarea.** Si cambia el objetivo, empieza otra.
- **Más de un fichero → plan primero** y espera confirmación.
- Para y pregunta cuando falte un dato que no puedas deducir: endpoints, claves,
  decisiones de producto. Nunca inventes un valor para desbloquearte.

## Implementación

- Alcance cerrado: haz lo que pide la tarea. Lo mejorable fuera de alcance se
  anota al final, no se implementa.
- **No toques código que ya funciona** salvo que la tarea lo pida o un test
  revele un bug real. Si encuentras uno, dilo antes de arreglarlo.
- Reutiliza lo que ya hay antes de añadir abstracciones o dependencias.
- Nunca inventes APIs, claves de configuración ni comportamiento de una
  biblioteca. Verifica lo que dependa de la versión.
- Sin base de datos en el camino de lectura. Sin framework de componentes en el
  frontend.
- Nunca expongas secretos. Las claves van a GitHub Secrets o a un `.env`
  ignorado, jamás al chat ni al repositorio.

## Validación

Lo más específico primero, después lo ancho. Los comandos son los que ejecuta
`.github/workflows/ci.yml`; si cambias uno, cambia los dos sitios.

`pytest` vive en el entorno virtual, no en el PATH global:

```bash
source .venv/bin/activate

pytest tests/test_merge.py -q       # el fichero que tocas
pytest                              # suite completa
pytest --cov                        # falla por debajo del 85 % (RNF-10)
ruff check src/ tests/ scripts/     # el alcance que usa CI

# El humo necesita datos de demostración: sin el primero, el segundo no corre.
PYTHONPATH=src python scripts/build_demo_data.py
PYTHONPATH=src python scripts/smoke_test.py

cd web
npm run check                       # tsc --noEmit
npm run build
npm run e2e                         # Playwright
```

`ruff format` **no** es una puerta: el repositorio no está formateado con él y
CI no lo comprueba. No lo pases en masa —reformatearía 47 ficheros y enterraría
tu cambio en el diff—.

Si tocas `export.py` o `merge.py`, ejecuta también `tests/test_invariants.py`:
los nueve invariantes se validan antes de publicar.

Antes de un `npm run e2e` tras cambiar el frontend: `pkill -f "vite preview"`.
Playwright reutiliza el servidor vivo y te hace pasar tests contra un `dist`
viejo.

**No declares éxito sin salida de comando.** Un test saltado, un aviso o un
comando fallido son evidencia y se reportan.

## Comunicación

- Terso y técnico. Sin cortesías ni resúmenes repetidos.
- Devuelve: decisiones, ficheros tocados, validación ejecutada y riesgos vivos.
- **Resume los logs.** Solo las líneas decisivas y el comando para reproducir.

## Autonomía

Sin preguntar: leer, buscar, editar dentro del alcance, ejecutar pruebas y lint.

Preguntando antes: borrar, escribir fuera del repo, tocar CI, infraestructura,
secretos o configuración de producción, y añadir dependencias de producción.

## Contexto

- Recupera ficheros bajo demanda; no precargues carpetas enteras.
- Lo duradero va a `docs/` o a un ADR en `.ai/decisions/`. Lo temporal, a un
  handoff en `.ai/handoffs/`.
- Subagentes solo para trabajo acotado, independiente y de lectura. Devuelven
  resúmenes, nunca trazas crudas.

## Convenciones

- Python 3.12, `ruff` para formato y lint.
- Comentarios en castellano, explicando el **porqué**, no el qué.
- Cada fuente externa necesita su fixture de regresión en `tests/fixtures/`.
- Cuando un parseo falle en producción, el payload que lo rompió se convierte en
  fixture **antes** de arreglar el código.
- Conventional commits. Una rama por hito; no se trabaja sobre `main`.
