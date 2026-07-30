# Contexto del proyecto

Léeme antes de tocar nada.

## Qué es esto

Visor público de incendios forestales activos en España. Combina detecciones
satelitales (NASA FIRMS, EUMETSAT SEVIRI) con partes oficiales de los servicios
autonómicos de extinción.

El pipeline de FIRMS ya está implementado y probado. Lo que falta está
especificado en `docs/ESPECIFICACION.md`.

## Orden de lectura

1. **`docs/ESPECIFICACION.md`** — requisitos numerados, pruebas y criterios de
   aceptación. Es el contrato. Empieza por las secciones 0 a 4.
2. **`README.md`** — arquitectura y decisiones ya tomadas, con su porqué.
3. **`src/incendios/config.py`** — todos los parámetros ajustables en un sitio.
4. **`src/incendios/merge.py`** — la lógica menos obvia del repo. Léela entera
   antes de modificar cualquier cosa de fusión.
5. **`docs/COMO-CONECTAR-LAS-FUENTES.md`** — lo que está bloqueado esperando
   datos, y su anexo técnico para escribir un adaptador nuevo.

## Estado actual

| Módulo | Estado |
|---|---|
| `firms.py` · ingesta NASA FIRMS | Completo, probado |
| `clean.py` · filtros y máscara industrial | Completo, probado |
| `cluster.py` · ST-DBSCAN y perímetros | Completo, probado |
| `merge.py` · fusión oficial ↔ satélite | Completo, probado |
| `enrich.py` · geocoding inverso | Completo, falta la capa del IGN |
| `export.py` · GeoJSON, PMTiles, Parquet | Completo, probado |
| `sources/` · adaptadores oficiales | Framework listo, **endpoints sin descubrir** |
| `web/index.html` | Prototipo. La v1 real está en la especificación |
| SEVIRI, EFFIS, viento, SEO, alertas | Sin empezar |

Prueba de humo sin red: `PYTHONPATH=src python scripts/smoke_test.py`

## Reglas duras

**No inventes endpoints.** Los adaptadores de `sources/adapters.py` tienen la URL
vacía a propósito. Una URL falsa devuelve 404 en silencio y eso se lee como "hoy
no hay incendios" — el fallo más peligroso de este sistema. Si necesitas un
endpoint, pídelo.

**No publiques salidas vacías.** Si una fuente devuelve cero registros y el
histórico reciente tenía cientos, es un fallo de la fuente, no ausencia de
incendios. Abortar sin sobrescribir.

**Un fallo de fuente no tumba el pipeline.** El `try` de `OfficialSource.collect`
está ahí por eso. No lo quites.

**La latencia se publica siempre.** Hay dos números distintos —edad del dato
satelital y edad de la última ejecución— y ambos se muestran. Mezclarlos induce a
error y es el fallo que este proyecto existe para no cometer.

**Nada de base de datos en el camino de lectura.** El frontend lee ficheros
estáticos de CDN. Si crees que necesitas una BD, para y justifícalo.

**Sin framework de componentes en el frontend.** Es un mapa con paneles.

## Invariantes

Los ocho de la sección 4.4 de la especificación se validan antes de publicar. Si
tocas `export.py` o `merge.py`, ejecuta `test_invariants.py`.

## Aviso de dominio

Esto lo va a mirar gente asustada buscando si arde algo cerca de su casa. Ante la
duda entre mostrar un dato incierto o no mostrarlo, no se muestra. El lenguaje de
la interfaz usa "estimación" y "detección", nunca verbos de certeza. El aviso de
que no sustituye al 112 es permanente y no se oculta en ninguna resolución.

## Proceso de trabajo

**Plan antes de código.** Ante cualquier tarea de más de un fichero, primero
devuelve el plan: qué ficheros vas a crear o tocar y qué hace cada uno. Espera
confirmación.

**Una rama por hito.** `hito-2-frontend`, `hito-3-fuentes`. No trabajes sobre
`main`.

**No toques código que ya funciona** salvo que la tarea lo pida o un test revele
un bug real. Si encuentras uno, dilo antes de arreglarlo.

**Alcance cerrado.** Haz lo que pide la tarea. Si ves algo mejorable fuera de
alcance, anótalo al final de tu respuesta en vez de implementarlo.

**Para y pregunta** cuando falte un dato que no puedes deducir: endpoints,
claves, credenciales, decisiones de producto. Nunca inventes un valor para
desbloquearte.

## Pruebas

`pytest` desde la raíz. La configuración vive en `pyproject.toml`, que ya añade
`src/` y `tests/` al path: no hace falta `PYTHONPATH`.

```
pytest                  # suite completa
pytest --cov            # con cobertura, falla por debajo del 85 % (RNF-10)
pytest tests/test_merge.py -q
```

Marcadores en uso:

- `xfail(strict=True)` — el comportamiento que exige la especificación y que el
  código **todavía no cumple**. Si alguien lo arregla, el test se pone verde y
  `strict` obliga a quitar el marcador. No es deuda escondida: es deuda que
  avisa.
- `skip` — requisito de un hito posterior o dependencia externa que no está en
  el repo (capa del IGN, endpoints autonómicos). El motivo cita siempre el ID
  del requisito.

Prueba de humo sin red: `PYTHONPATH=src python scripts/smoke_test.py`

## Convenciones

- Python 3.12, `ruff` para formato y lint.
- Comentarios en castellano, explicando el **porqué**, no el qué.
- Cobertura mínima del pipeline: 85 %.
- Cada fuente externa necesita su fixture de regresión en `tests/fixtures/`.
- Cuando un parseo falle en producción, el payload que lo rompió se convierte en
  fixture **antes** de arreglar el código.
