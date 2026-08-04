/**
 * Paneles de cabecera y barra lateral · RF-F-05, RF-F-06, RF-F-13.
 *
 * Sin framework de componentes, por decisión de la sección 2.2: esto es un mapa
 * con paneles y añadir 40 KB de runtime para pintar listas no se sostiene. Cada
 * función recibe su nodo y lo repinta entero; con estos volúmenes el coste de
 * `innerHTML` es irrelevante y el código queda legible.
 */

import {
  duracion,
  edadDesde,
  etiquetaEstadoFuente,
  haceCuanto,
  iconoEstadoFuente,
  nivelLatencia,
  nombreFuente,
  numero,
} from '../formato';
import type { Fuente, Manifiesto, Salud } from '../tipos';

function texto(valor: string): string {
  const d = document.createElement('div');
  d.textContent = valor;
  return d.innerHTML;
}

/**
 * RF-F-05 · los dos números.
 *
 * `worst_data_age_seconds` es la antigüedad del dato más viejo que se está
 * mostrando, no la del más nuevo: enseñar el mejor caso sería tranquilizar sin
 * fundamento.
 */
export function pintarLatencia(manifiesto: Manifiesto | null): void {
  const valorDato = document.getElementById('latencia-dato')!;
  const pieDato = document.getElementById('latencia-dato-pie')!;
  const valorPipeline = document.getElementById('latencia-pipeline')!;

  if (!manifiesto) {
    valorDato.textContent = '—';
    valorDato.dataset.nivel = 'bad';
    pieDato.textContent = 'sin datos';
    valorPipeline.textContent = '—';
    valorPipeline.dataset.nivel = 'bad';
    return;
  }

  const edadDato = manifiesto.worst_data_age_seconds;
  valorDato.textContent = duracion(edadDato);
  valorDato.dataset.nivel = nivelLatencia(edadDato);

  // Se desglosa por sensor, no solo el peor.
  //
  // Con un único número, «19 h 41 min» se lee como que TODO está viejo. El
  // 31-07-2026 esa era la cifra y MODIS tenía 5,6 h: lo que estaba parado era
  // VIIRS. Enseñar los dos convierte una alarma difusa en un diagnóstico.
  //
  // El titular sigue siendo el peor caso, que es la regla del proyecto: enseñar
  // el mejor sería tranquilizar sin fundamento.
  const familias = Object.entries(manifiesto.data_age_seconds ?? {})
    .sort((a, b) => b[1] - a[1]);

  if (!familias.length) {
    pieDato.textContent = 'sin datos';
  } else if (familias.length === 1) {
    pieDato.textContent = `último dato de ${nombreFuente(familias[0][0])}`;
  } else {
    pieDato.textContent = familias
      .map(([id, edad]) => `${nombreFuente(id)} ${duracion(edad)}`)
      .join(' · ');
  }

  // La edad real de la ejecución se recalcula desde `generated_at`, no se lee
  // de `pipeline_age_seconds`: ese número envejece mientras la pestaña sigue
  // abierta y quedarse con el del fichero congelaría el reloj.
  const edadEjecucion = edadDesde(manifiesto.generated_at) ?? manifiesto.pipeline_age_seconds;
  valorPipeline.textContent = haceCuanto(edadEjecucion);
  valorPipeline.dataset.nivel = nivelLatencia(edadEjecucion);
}

export type TonoBanda = 'error' | 'aviso' | 'demo';

export function pintarBanda(mensaje: string | null, tono: TonoBanda = 'aviso'): void {
  const banda = document.getElementById('banda')!;
  if (!mensaje) {
    banda.hidden = true;
    banda.textContent = '';
    return;
  }
  banda.hidden = false;
  banda.dataset.tono = tono;
  banda.innerHTML = mensaje;
}

/** RF-F-06 · una fila por fuente, las caídas arriba (ya vienen ordenadas). */
export function pintarFuentes(salud: Salud | null): void {
  const lista = document.getElementById('lista-fuentes')!;

  if (!salud || salud.sources.length === 0) {
    lista.innerHTML = '<li class="vacio">Estado de fuentes no disponible</li>';
    return;
  }

  // Se reordena aquí aunque el pipeline ya lo haga. Confiar en el orden del
  // fichero significa que cualquier cosa que lo reordene —una caché, un
  // proxy, un cambio en `health.py`— hunde las fuentes caídas al final del
  // panel sin que nadie se entere. La garantía de RF-F-06 tiene que ser local.
  const PRIORIDAD: Record<string, number> = { error: 0, stale: 1, disabled: 2, ok: 3 };
  const ordenadas = [...salud.sources].sort(
    (a, b) =>
      (PRIORIDAD[a.status] ?? 9) - (PRIORIDAD[b.status] ?? 9) ||
      a.name.localeCompare(b.name, 'es'),
  );

  lista.innerHTML = ordenadas.map(filaFuente).join('');
}

function filaFuente(f: Fuente): string {
  const edad = f.age_seconds === null ? 'sin éxito reciente' : haceCuanto(f.age_seconds);
  // `stale_reason` manda cuando existe: dice **quién** ha fallado. «rancio» a
  // secas no distingue «no hemos podido descargar» de «la fuente ha dejado de
  // publicar», y solo el primero se arregla desde aquí.
  //
  // El caso que lo motiva: el 31-07-2026 FIRMS dejó de servir VIIRS y el panel
  // decía «correcto · 883 registros · hace 15 s», porque la descarga del
  // archivo de tres días seguía funcionando.
  const detalle =
    f.status === 'error'
      ? texto(f.error ?? 'sin respuesta')
      : f.status === 'disabled'
        ? 'endpoint sin configurar'
        : f.stale_reason
          ? texto(f.stale_reason)
          : `${numero(f.records)} registros · ${edad}`;

  return `
    <li class="fuente fuente--${f.status}">
      <span class="fuente__punto" data-estado="${f.status}" aria-hidden="true"></span>
      <span>
        <span class="fuente__nombre">${texto(f.name)}</span>
        <span class="fuente__estado">
          <span aria-hidden="true">${iconoEstadoFuente(f.status)}</span>
          ${etiquetaEstadoFuente(f.status)} · ${detalle}
        </span>
        ${margen(f)}
      </span>
      <span class="fuente__meta">${f.status === 'ok' ? duracion(f.age_seconds) : ''}</span>
    </li>`;
}

/**
 * Margen de posición declarado por la fuente y, si lo hay, el medido.
 *
 * Por qué se enseña. `precision_m` es lo que **dibuja el radio del círculo** de
 * cada incendio en el mapa, y hasta ahora no aparecía en ninguna parte: le
 * decíamos al usuario «está en algún punto de este círculo» sin decir de dónde
 * salía el círculo.
 *
 * Y por qué se enseñan los dos juntos. Los valores declarados están puestos a
 * ojo —500 m para JCyL, 100 m para el 112— y sus propios comentarios en
 * `adapters.py` dicen «provisional hasta medirlo». Poner al lado lo que de
 * verdad se separan el parte oficial y la detección satelital hace que la
 * discrepancia se vea sola, en vez de quedarse en un comentario del código.
 *
 * Se dice **en cuántos partes** porque un número medido sobre uno solo no es
 * una medición, y omitir la muestra invitaría a leerlo como si lo fuera.
 */
function margen(f: Fuente): string {
  if (f.precision_m === null) return '';

  const declarado = `margen declarado ${numero(f.precision_m)} m`;

  // Nulo y cero son cosas distintas: `null` es «esta fuente no llega a la
  // fusión», `0` es «llegó y ninguno de sus partes coincidió con un foco».
  // Ninguno de los dos da una medida, pero solo el segundo dice algo.
  if (!f.emparejados || f.separacion_mediana_m === null) {
    return `<span class="fuente__margen">${declarado}</span>`;
  }

  const partes = f.emparejados === 1 ? '1 parte' : `${numero(f.emparejados)} partes`;
  return `
    <span class="fuente__margen">
      ${declarado} ·
      <b>medido ${numero(f.separacion_mediana_m)} m</b> en ${partes}
    </span>`;
}

export function pintarResumen(manifiesto: Manifiesto | null): void {
  const lista = document.getElementById('resumen')!;
  if (!manifiesto) {
    lista.innerHTML = '<li class="vacio">Sin datos</li>';
    return;
  }

  const c = manifiesto.counts;
  const filas: Array<[number, string]> = [
    [c.incidents_total, 'incidentes detectados'],
    [c.incidents_satellite_confirmed, 'con confirmación satelital'],
    [c.incidents_official_only, 'solo con parte oficial'],
    [c.hotspots_24h, 'focos satelitales · 24 h'],
    // Registrar lo suprimido es obligatorio (riesgo 3 de la sección 11): si la
    // máscara industrial llegara a ocultar un incendio real, este número es la
    // única pista de que algo se ha filtrado.
    [c.hotspots_suppressed_industrial, 'suprimidos · foco industrial'],
    [c.hotspots_suppressed_lowconf, 'suprimidos · confianza baja'],
  ];

  lista.innerHTML = filas
    .map(([n, etiqueta]) => `<li><b>${numero(n)}</b> ${etiqueta}</li>`)
    .join('');
}

export function pintarAtribucion(salud: Salud | null): void {
  const nodo = document.getElementById('atribucion')!;
  const fuentes = new Set<string>([
    'NASA FIRMS',
    'EUMETSAT LSA-SAF',
    'Open-Meteo (CC BY 4.0)',
    'OpenStreetMap (ODbL)',
  ]);
  for (const f of salud?.sources ?? []) {
    if (f.attribution) fuentes.add(f.attribution);
  }
  nodo.textContent = [...fuentes].join(' · ');
}
