# Hallazgos de la sesión 1 · suite de pruebas

La suite se escribió sin tocar `src/`, según el encargo. Cuatro casos que la
tabla 8.1 exige **no se cumplen** con el código actual. Están escritos como
`xfail(strict=True)`, así que:

- hoy la suite pasa en verde y el fallo queda registrado;
- el día que alguien arregle el código, el test se pone verde, `strict` hace
  fallar la suite y obliga a quitar el marcador.

Ninguno es deuda escondida. Ordenados por gravedad.

---

## 1 · GRAVE — dos partes oficiales se emparejan con el mismo cluster

**Dónde:** `src/incendios/merge.py::match`
**Test:** `tests/test_merge.py::test_does_not_merge_neighbours`
**Requisito:** RF-P-06, que exige este test por nombre.

`gpd.sjoin_nearest` va de oficial → incendio. Cada parte oficial busca su cluster
más próximo de forma independiente y **nada impide que dos elijan el mismo**.

Reproducción: dos avisos de 112 CV separados 800 m, un solo cluster FIRMS en
medio. Los dos se emparejan:

```
external_id fire_id  match_distance_m
          A      f1             111.0
          B      f1             688.0     ← no debería emparejarse
```

**Consecuencia:** los dos partes reciben el mismo `fire_id`, así que
`build_incidents` produce **un solo incidente donde hay dos incendios**. El
segundo no aparece en el mapa. No es un duplicado que se limpia: es un incendio
real que el satélite todavía no ha visto y que desaparece del visor.

Es exactamente el modo de fallo que la sección 1.3 dice evitar y el peor de los
cuatro, porque es silencioso: nada en el log indica que se ha perdido nada.

**Arreglo:** tras el filtro por tolerancia y ventana temporal, agrupar por
`fire_id` y conservar solo el de `_dist` mínima. Los demás vuelven a
`fire_id = None` y siguen su camino como huérfanos oficiales, que es lo que ya
hace `build_incidents` con ellos. Unas cuatro líneas.

---

## 2 · Un campo renombrado por una comunidad no avisa

**Dónde:** `src/incendios/sources/base.py::_finalize` y `adapters.py::parse`
**Test:** `tests/test_sources.py::test_renamed_field_warns_instead_of_silent_nulls`
**Requisito:** tabla 8.1, "Aviso explícito, no fila silenciosa con nulos".

Si una comunidad renombra `ESTADO` a `ESTADO_V2`, `props.get("ESTADO")` devuelve
`None`, `norm_status(None)` devuelve `"desconocido"` — que **es un estado
válido**, así que no entra en la rama que avisa — y `_finalize` no comprueba en
ningún momento si los campos del `field_map` existen en el payload.

**Consecuencia:** la fuente sigue apareciendo `ok` en `sources.json`, con su
número de registros correcto, y todas las filas vacías. Es el riesgo 1 de la
sección 11 ("una comunidad cambia el formato → fuente perdida sin aviso")
materializado, y la mitigación prevista —fixtures de regresión— no lo cubre,
porque el fixture guardado sigue pasando: es el payload *nuevo* el que cambió.

**Arreglo:** en `parse`, comprobar que cada campo del `field_map` aparece en al
menos una feature del payload; si no, `log.warning` con el nombre del campo y el
`source_id`. El estado de la fuente debería pasar a `error` o `stale`, no
quedarse en `ok`.

---

## 3 · `KeyError` opaco cuando FIRMS cambia de esquema

**Dónde:** `src/incendios/firms.py::_normalize`
**Test:** `tests/test_firms.py::test_missing_column_raises_clear_error`
**Requisito:** tabla 8.1, "Excepción clara, no `KeyError` opaco".

Falta una columna obligatoria y sale `KeyError: 'latitude'`. Sin sensor, sin
área, sin la lista de columnas que sí llegaron.

No es un fallo de corrección —el pipeline aborta, que es lo correcto— sino de
diagnóstico. En pico de temporada, con el cron cada 10 minutos, la diferencia
entre este mensaje y uno que diga qué combinación sensor/bbox rompió y con qué
esquema son un par de horas de depuración.

Nótese que **el caso realmente peligroso ya está bien resuelto**: la respuesta
no-CSV con HTTP 200 (clave agotada) se detecta por contenido y devuelve vacío sin
lanzar. Eso está cubierto y probado.

**Arreglo:** validar `SCHEMA` contra las columnas presentes al entrar en
`_normalize` y lanzar `ValueError` con el sensor, el área y el diff de columnas.

---

## 4 · Columna opcional ausente rompe con `AttributeError`

**Dónde:** `src/incendios/firms.py::_normalize`, líneas 101 y 105
**Test:** `tests/test_firms.py::test_optional_column_absent_does_not_crash`

```python
out["daynight"] = df.get("daynight", "").astype(str)
```

`df.get("daynight", "")` devuelve **el string por defecto**, no una Serie vacía,
así que `.astype(str)` lanza `AttributeError: 'str' object has no attribute
'astype'`. Mismo patrón en la línea 101: si no hay ni `bright_ti4` ni
`brightness`, `df.get("brightness")` devuelve `None` y falla igual.

Es latente: el CSV real de FIRMS siempre trae `daynight`. Pero el `.get()` con
valor por defecto está escrito precisamente para tolerar su ausencia, y no la
tolera.

**Arreglo:** `df["daynight"].astype(str) if "daynight" in df.columns else ""`.

---

## Lo que sí está bien y ahora tiene red

Conviene decirlo, porque el listado de arriba da una impresión sesgada:

- La detección de respuesta no-CSV de FIRMS (el fallo más peligroso del sistema
  según la sección 0) funciona y está probada en cuatro variantes.
- La máscara industrial suprime Puertollano y conserva lo que hay a 5 km. Los
  dos sentidos probados, que es lo que importa en un filtro que puede ocultar
  incendios reales.
- `_worst_status` elige bien el estado más grave en las cinco combinaciones
  probadas.
- La tolerancia de fusión escala con `precision_m` y tiene tope. INFOCAM a 6 km
  empareja; 112 CV a 6 km no.
- Los huérfanos oficiales sobreviven con `fire_id` estable y sin colisión
  posible con los de FIRMS.
- `fire_id` es estable ante reordenación de filas, que es de lo que depende el
  enlace permanente de RF-F-02.
- El histórico es idempotente sobre tres escrituras seguidas.
- Una fuente caída devuelve vacío sin lanzar, en 500, timeout y JSON corrupto.

---

## Pendientes que no son fallos

Marcados `skip`, con el requisito que los desbloquea:

| Test | Espera a |
|---|---|
| `test_aborts_on_suspicious_emptiness` | RF-P-11 (sesión 3) |
| `test_invariant_violation_aborts_publication` ×8 | RF-P-14 (sesión 2) |
| `test_seviri_does_not_overmerge` | RF-P-02 + RF-P-05 (hito 4) |
| `test_twenty_known_coordinates_against_ign` | capa municipal del IGN en `config/` |

El de SEVIRI merece una nota: escrito hoy **pasaría en verde sin probar nada**.
Con `eps_m` global de 1500 m, dos focos a 4 km no se fusionan de ninguna manera,
haya o no un hotspot entre medias. El test solo tiene sentido cuando exista
`instrument="SEVIRI"` con `precision_m=3000` y un `eps` efectivo por sensor.
