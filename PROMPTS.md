# Prompts por hito

Listos para pegar. Uno por sesión. No los encadenes: cierra un hito, revisa el
diff, commitea, y abre sesión nueva para el siguiente.

Regla general: **nunca "haz lo que veas conveniente"**. Esa instrucción produce
diffs de cuarenta ficheros que no puedes revisar. `CLAUDE.md` le da contexto y
límites; la dirección se la das tú aquí.

---

## Sesión 1 · Suite de pruebas ✅ hecha

Primer encargo a propósito: el código ya existe, el resultado es verificable de
un vistazo (`pytest` pasa o no), y no puede romper nada porque tiene prohibido
tocar `src/`. Deja la red de seguridad puesta antes de que empiece a escribir.

```
Lee @docs/ESPECIFICACION.md (secciones 0-4 y 8) y @src/incendios/merge.py.

Tarea: implementar la suite de pruebas unitarias de la sección 8.1
para los módulos ya existentes: firms, clean, cluster, merge, export.

Restricciones:
- No modifiques nada de src/. Si un test revela un bug real, párate
  y dímelo antes de arreglarlo.
- Un fichero por módulo, en tests/.
- Prioriza los casos de fallo de la tabla 8.1 sobre los caminos felices.
- Sin red: fixtures o datos sintéticos. Usa @scripts/smoke_test.py como
  referencia de cómo generar hotspots sintéticos.
- Añade pytest y pytest-cov a requirements.txt.

Dame primero el plan: qué ficheros creas y qué casos cubre cada uno.
```

**Aceptación:** `pytest --cov` verde, cobertura ≥ 85 %, los 18 casos de la tabla
8.1 presentes.

**Resultado:** 96 % de cobertura. Los casos que el código actual no cumple
quedaron como `xfail(strict=True)`, documentados en
`docs/ERRORES-Y-SOLUCIONES.md`. `src/` sin tocar.

---

## Sesión 2 · Validación de invariantes

```
Lee @docs/ESPECIFICACION.md sección 4.4 y @src/incendios/export.py.

Tarea: implementar RF-P-14. Un módulo src/incendios/validate.py que
compruebe los 8 invariantes sobre incidents.geojson antes de publicar.
Si alguno falla, abortar con código de salida distinto de cero.

Integrarlo en export_all() y en el workflow.

Pruebas: tests/test_invariants.py con un caso por invariante violado
(8 pruebas), cada una verificando que aborta.
```

**Ojo:** `merge.build_incidents()` todavía no emite `official_confirmed`,
`position_precision_m`, `id` ni `intensity` del contrato 4.3. Sin esos campos
los invariantes 2, 3, 6 y 7 no son comprobables. Hay 8 tests ya escritos y
marcados `skip` en `tests/test_export.py` esperando este módulo.

---

## Sesión 3 · Arranque en producción (hito 1)

```
Lee @docs/ESPECIFICACION.md hito 1 y @.github/workflows/ingest.yml.

Tarea: dejar el pipeline listo para correr en producción.
- Implementar RF-P-10 (sources.json) y RF-P-11 (publicación atómica).
- Añadir el aborto por vaciado sospechoso: si el número de hotspots
  cae más de un 90 % respecto a la mediana de las últimas 24 ejecuciones,
  abortar sin sobrescribir.
- Alerta si worst_data_age_seconds > 14400.
- Ajustar el cron a */10.

Pruebas para cada punto. No toques el clustering ni la fusión.
```

`tests/test_export.py::test_aborts_on_suspicious_emptiness` ya está escrito y
marcado `skip`.

---

## Sesión 4 · Frontend v1 (hito 2)

Este sí es escritura desde cero. Pídele el plan de diseño antes del código.

```
Lee @docs/ESPECIFICACION.md (secciones 6, 7, 9) y @src/incendios/export.py
para el contrato exacto de propiedades. @web/index.html es un prototipo
desechable: puedes reemplazarlo entero.

Tarea: frontend v1 en web/. Vite + TypeScript, MapLibre GL, sin framework
de componentes.

Alcance: RF-F-01, 02, 03, 04, 05, 10, 12, 13. Nada más.
No implementes todavía la lista lateral, el buscador ni los filtros.

Presta atención especial a:
- RF-F-05: DOS latencias distintas, visibles a la vez.
- RF-F-03: el anillo de incertidumbre dibujado a partir de
  position_precision_m. Es la diferencia clave del producto.
- RF-F-04: prohibido el badge numérico de clustering a zoom bajo.

Antes de escribir código, dame el plan de diseño: paleta con hex,
tipografías con su papel, estructura de la interfaz, y qué elemento
va a ser el que se recuerde. Espera mi confirmación.
```

---

## Sesión 5 · Capturas y E2E

**Bloqueado** hasta que exista el frontend de la sesión 4. El arnés de capturas
no puede montarse contra un prototipo que se va a reemplazar entero.

```
Lee @docs/ESPECIFICACION.md secciones 8.4 y 9, y @tests/e2e/capturas.spec.ts.

Tarea: completar el arnés de capturas. Están montados el config, la
interceptación de red y tres escenarios de ejemplo. Faltan los demás
de la tabla 9.2.

Cada escenario:
- Datos de tests/fixtures/, nunca producción. Deterministas.
- Los tres viewports de 9.1.
- Nombre de fichero exacto de la tabla 9.2.

Añade también los escenarios E2E de 8.4 que aún no existan.
```

---

## Sesión 6 · Fuentes oficiales (hito 3)

**Bloqueado** hasta que tengas al menos un endpoint descubierto siguiendo
`docs/COMO-CONECTAR-LAS-FUENTES.md`.

```
Lee @docs/COMO-CONECTAR-LAS-FUENTES.md y @src/incendios/sources/

He descubierto el endpoint de {FUENTE}:
  URL: {url}
  Respuesta de ejemplo: {pega el JSON}

Tarea: configurar el adaptador de esa fuente.
- Rellenar el field_map con los nombres reales.
- Guardar la respuesta como tests/fixtures/{source_id}.json.
- Test de parseo contra el fixture.
- Test que inyecte un 500 y verifique que collect() devuelve vacío
  sin lanzar.

precision_m lo mido yo y te lo doy. Déjalo con el valor actual y
un TODO.
```

El framework ya está probado (`tests/test_sources.py`, 30 tests). Configurar una
fuente debería ser rellenar el `field_map` y añadir su fixture, nada más.

---

## Cuando algo se tuerce

- `git checkout .` y vuelves al último commit. Por eso commiteas tú.
- Si el diff es demasiado grande para revisarlo, es que el prompt era
  demasiado abierto. Trocea más.
- Si empieza a inventar endpoints o valores, recuérdale la sección de reglas
  duras de `CLAUDE.md`.
