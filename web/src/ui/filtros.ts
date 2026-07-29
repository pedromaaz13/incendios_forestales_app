/**
 * Filtros · RF-F-09.
 *
 * Todos se aplican con `setFilter` de MapLibre, que evalúa en GPU. Es la razón
 * de haber elegido MapLibre frente a Leaflet (sección 2.3): filtrar 50.000
 * features en menos de 100 ms —el umbral de RNF-04— es inviable reconstruyendo
 * una capa de marcadores en JavaScript.
 *
 * Ninguno provoca recarga de datos: los GeoJSON ya están en el cliente y lo
 * único que cambia es qué se pinta.
 */

import type { ExpressionSpecification, FilterSpecification, Map as MapaGL } from 'maplibre-gl';

import { CAPA_HOTSPOTS, CAPA_INCERTIDUMBRE, CAPA_INCIDENTES } from '../map/capas';

export type Periodo = 1 | 2 | 3;
export type Confianza = 'todas' | 'media' | 'alta';
export type Origen = 'todos' | 'oficial' | 'satelite';

export interface EstadoFiltros {
  periodo: Periodo;
  confianza: Confianza;
  sensores: Set<string>;
  origen: Origen;
}

export const FILTROS_INICIALES: EstadoFiltros = {
  periodo: 1,
  confianza: 'todas',
  sensores: new Set(['VIIRS', 'MODIS', 'SEVIRI']),
  origen: 'todos',
};

const UMBRAL_CONFIANZA: Record<Confianza, number> = {
  todas: 0,
  media: 50,
  alta: 80,
};

/**
 * Filtro de incidentes. El período se aplica sobre `last_detected`.
 *
 * La comparación se hace con la fecha ya calculada en JS y no con una
 * expresión de fecha de MapLibre porque el estilo no tiene funciones de tiempo:
 * las cadenas ISO-8601 en UTC son ordenables lexicográficamente, así que un
 * `>=` sobre texto da el resultado correcto y se evalúa igual de rápido.
 */
export function filtroIncidentes(f: EstadoFiltros): FilterSpecification {
  const desde = new Date(Date.now() - f.periodo * 86400_000).toISOString();

  const condiciones: ExpressionSpecification[] = [
    ['>=', ['coalesce', ['get', 'last_detected'], ''], desde],
  ];

  if (f.origen === 'oficial') {
    condiciones.push(['==', ['get', 'official_confirmed'], true]);
  } else if (f.origen === 'satelite') {
    condiciones.push(['==', ['get', 'satellite_confirmed'], true]);
  }

  return ['all', ...condiciones] as FilterSpecification;
}

/** Filtro de hotspots: período, confianza mínima y sensores conmutables. */
export function filtroHotspots(f: EstadoFiltros): FilterSpecification {
  const desde = new Date(Date.now() - f.periodo * 86400_000).toISOString();
  const sensores = [...f.sensores];

  return [
    'all',
    ['>=', ['coalesce', ['get', 'acq_dt'], ''], desde],
    ['>=', ['coalesce', ['get', 'confidence_pct'], 0], UMBRAL_CONFIANZA[f.confianza]],
    // Sin ningún sensor activo no se muestra nada, que es lo que el usuario ha
    // pedido de forma explícita al apagarlos todos.
    sensores.length
      ? (['in', ['coalesce', ['get', 'instrument'], 'VIIRS'], ['literal', sensores]] as ExpressionSpecification)
      : (['==', ['literal', 1], ['literal', 0]] as ExpressionSpecification),
  ] as FilterSpecification;
}

export function aplicar(
  mapa: MapaGL,
  f: EstadoFiltros,
  dia: string | null = null,
): void {
  // `!has point_count` va en todos los filtros de estas capas: sin él, al
  // aplicar un filtro se perdería la exclusión de los globos de grupo y los
  // incidentes agrupados se pintarían dos veces, como globo y como punto.
  const incidentes = [
    'all',
    ['!', ['has', 'point_count']],
    filtroIncidentes(f),
  ] as unknown as FilterSpecification;

  for (const capa of [CAPA_INCIDENTES, CAPA_INCERTIDUMBRE]) {
    if (mapa.getLayer(capa)) mapa.setFilter(capa, incidentes);
  }
  if (mapa.getLayer(CAPA_HOTSPOTS)) {
    // El día elegido en el evolutivo se aplica solo a los focos. Los incidentes
    // no se tocan: un incendio puede arder varios días y esconderlo porque su
    // última detección no cae en el día elegido daría a entender que no existía.
    const porDia = dia
      ? ([
          'all',
          filtroHotspots(f),
          ['==', ['slice', ['coalesce', ['get', 'acq_dt'], ''], 0, 10], dia],
        ] as unknown as FilterSpecification)
      : filtroHotspots(f);
    mapa.setFilter(CAPA_HOTSPOTS, porDia);
  }
}

/**
 * Predicado equivalente para la lista lateral.
 *
 * La lista se pinta en JS, así que necesita la misma lógica por su cuenta. Que
 * el mapa y la lista discrepen sería peor que no tener filtros: alguien vería
 * una tarjeta de un incendio que no aparece en el mapa y no sabría a cuál creer.
 */
export function pasaElFiltro(
  props: { last_detected: string | null; official_confirmed: boolean; satellite_confirmed: boolean },
  f: EstadoFiltros,
): boolean {
  const desde = new Date(Date.now() - f.periodo * 86400_000).toISOString();
  if (!props.last_detected || props.last_detected < desde) return false;
  if (f.origen === 'oficial' && !props.official_confirmed) return false;
  if (f.origen === 'satelite' && !props.satellite_confirmed) return false;
  return true;
}

export interface OpcionesFiltros {
  alCambiar: (f: EstadoFiltros) => void;
}

export function construirControles(
  nodo: HTMLElement,
  estado: EstadoFiltros,
  opciones: OpcionesFiltros,
): void {
  nodo.innerHTML = `
    <div class="filtro">
      <span class="filtro__rotulo" id="rot-periodo">Período</span>
      <div class="segmentado" role="radiogroup" aria-labelledby="rot-periodo" data-grupo="periodo">
        ${[1, 2, 3]
          .map(
            (d) =>
              `<button type="button" role="radio" data-valor="${d}"
                 aria-checked="${estado.periodo === d}">${d} día${d > 1 ? 's' : ''}</button>`,
          )
          .join('')}
      </div>
    </div>

    <div class="filtro">
      <span class="filtro__rotulo" id="rot-confianza">Confianza mínima</span>
      <div class="segmentado" role="radiogroup" aria-labelledby="rot-confianza" data-grupo="confianza">
        ${(
          [
            ['todas', 'Todas'],
            ['media', '≥ Media'],
            ['alta', 'Solo alta'],
          ] as const
        )
          .map(
            ([v, t]) =>
              `<button type="button" role="radio" data-valor="${v}"
                 aria-checked="${estado.confianza === v}">${t}</button>`,
          )
          .join('')}
      </div>
    </div>

    <div class="filtro">
      <span class="filtro__rotulo" id="rot-origen">Origen</span>
      <div class="segmentado" role="radiogroup" aria-labelledby="rot-origen" data-grupo="origen">
        ${(
          [
            ['todos', 'Todos'],
            ['oficial', 'Oficial'],
            ['satelite', 'Satélite'],
          ] as const
        )
          .map(
            ([v, t]) =>
              `<button type="button" role="radio" data-valor="${v}"
                 aria-checked="${estado.origen === v}">${t}</button>`,
          )
          .join('')}
      </div>
    </div>

    <div class="filtro" data-filtro="sensores">
      <span class="filtro__rotulo">Sensor</span>
      <div class="conmutadores" data-grupo="sensores">
        ${['VIIRS', 'MODIS', 'SEVIRI']
          .map(
            (s) =>
              `<button type="button" class="conmutador" data-valor="${s}"
                 aria-pressed="${estado.sensores.has(s)}">${s}</button>`,
          )
          .join('')}
      </div>
      <!-- El filtro de sensor solo afecta a los focos, y los focos aparecen a
           partir de zoom 9 (RF-F-04). Sin este aviso el botón parece roto: se
           pulsa, cambia de color y no pasa nada en el mapa. -->
      <span class="filtro__nota" id="nota-sensores" hidden>
        Afecta a los focos satelitales, que se muestran al acercar el mapa.
      </span>
    </div>`;

  for (const grupo of nodo.querySelectorAll<HTMLElement>('[data-grupo]')) {
    const clave = grupo.dataset.grupo!;

    for (const boton of grupo.querySelectorAll<HTMLButtonElement>('button')) {
      boton.addEventListener('click', () => {
        const valor = boton.dataset.valor!;

        if (clave === 'sensores') {
          const activo = boton.getAttribute('aria-pressed') !== 'true';
          boton.setAttribute('aria-pressed', String(activo));
          if (activo) estado.sensores.add(valor);
          else estado.sensores.delete(valor);
        } else {
          for (const otro of grupo.querySelectorAll('button')) {
            otro.setAttribute('aria-checked', String(otro === boton));
          }
          if (clave === 'periodo') estado.periodo = Number(valor) as Periodo;
          if (clave === 'confianza') estado.confianza = valor as Confianza;
          if (clave === 'origen') estado.origen = valor as Origen;
        }

        opciones.alCambiar(estado);
      });
    }
  }
}


/**
 * Avisa cuando el filtro de sensor no puede tener efecto visible.
 *
 * Los focos aparecen a partir de zoom 9 por decisión de RF-F-04: a zoom bajo
 * son ruido. Pero eso deja el control de sensor sin efecto observable la mayor
 * parte del tiempo, y un botón que cambia de color sin que pase nada se lee
 * como roto. Se atenúa y se explica en lugar de deshabilitarlo, porque la
 * elección sigue siendo válida y se aplicará en cuanto se acerque.
 */
export function avisarSensoresSegunZoom(zoom: number, zoomFocos: number): void {
  const bloque = document.querySelector<HTMLElement>('[data-filtro="sensores"]');
  const nota = document.getElementById('nota-sensores');
  if (!bloque || !nota) return;

  const inactivo = zoom < zoomFocos;
  bloque.dataset.inactivo = String(inactivo);
  nota.hidden = !inactivo;
}
