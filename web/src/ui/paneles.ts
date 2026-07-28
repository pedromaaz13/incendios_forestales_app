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

  // Se nombra el sensor que marca el peor caso: "2 h 19 min" sin decir de qué
  // no permite juzgar si es normal (VIIRS entre pasadas) o un problema.
  const familias = Object.entries(manifiesto.data_age_seconds ?? {});
  const peor = familias.sort((a, b) => b[1] - a[1])[0];
  pieDato.textContent = peor ? `último dato de ${nombreFuente(peor[0])}` : 'sin datos';

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

  lista.innerHTML = salud.sources.map(filaFuente).join('');
}

function filaFuente(f: Fuente): string {
  const edad = f.age_seconds === null ? 'sin éxito reciente' : haceCuanto(f.age_seconds);
  const detalle =
    f.status === 'error'
      ? texto(f.error ?? 'sin respuesta')
      : f.status === 'disabled'
        ? 'endpoint sin configurar'
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
      </span>
      <span class="fuente__meta">${f.status === 'ok' ? duracion(f.age_seconds) : ''}</span>
    </li>`;
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
