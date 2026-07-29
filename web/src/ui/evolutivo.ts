/**
 * Evolución diaria de la actividad detectada.
 *
 * Se calcula en el cliente a partir de `hotspots.geojson`, que ya trae la
 * ventana de 3 días que descarga el pipeline. No hace falta ningún dato nuevo.
 *
 * **Qué se cuenta y por qué no son hectáreas.** Cada barra es el número de
 * focos detectados ese día, que es una medición directa. La superficie sí se
 * muestra, pero como cifra secundaria y siempre etiquetada de estimación:
 * sale de multiplicar focos por el área nominal del píxel VIIRS, es una cota
 * inferior grosera, y un gráfico de barras titulado "hectáreas quemadas"
 * convertiría esa aproximación en un dato con aspecto de medido.
 *
 * **El último día casi siempre baja.** No es que se apague: es que el día en
 * curso está incompleto y los satélites polares no han terminado sus pasadas.
 * La barra parcial se dibuja rayada y se explica, porque leer esa caída como
 * mejoría es el error fácil de este gráfico.
 */

import { numero } from '../formato';
import type { PropiedadesHotspot } from '../tipos';

/** Superficie nominal de un píxel VIIRS de 375 m, en hectáreas. */
const HA_POR_FOCO = 14.06;

export interface DiaEvolutivo {
  dia: string;
  etiqueta: string;
  focos: number;
  hectareas: number;
  parcial: boolean;
}

export function agruparPorDia(
  hotspots: Array<Pick<PropiedadesHotspot, 'acq_dt'>>,
  ahora = new Date(),
): DiaEvolutivo[] {
  const cuenta = new Map<string, number>();

  for (const h of hotspots) {
    if (!h.acq_dt) continue;
    const dia = h.acq_dt.slice(0, 10);
    cuenta.set(dia, (cuenta.get(dia) ?? 0) + 1);
  }

  const hoy = ahora.toISOString().slice(0, 10);

  return [...cuenta.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([dia, focos]) => ({
      dia,
      etiqueta: `${dia.slice(8, 10)}/${dia.slice(5, 7)}`,
      focos,
      hectareas: Math.round(focos * HA_POR_FOCO),
      parcial: dia === hoy,
    }));
}

export interface OpcionesEvolutivo {
  /** Se llama al pulsar una barra. `null` deselecciona y vuelve a todos. */
  alElegirDia: (dia: string | null) => void;
}

export function pintarEvolutivo(
  nodo: HTMLElement,
  dias: DiaEvolutivo[],
  seleccionado: string | null,
  opciones: OpcionesEvolutivo,
): void {
  if (dias.length === 0) {
    nodo.innerHTML = '<p class="vacio">Sin focos en la ventana de datos.</p>';
    return;
  }

  const maximo = Math.max(...dias.map((d) => d.focos), 1);
  const hayParcial = dias.some((d) => d.parcial);

  nodo.innerHTML = `
    <div class="evolutivo__barras" role="group" aria-label="Focos detectados por día">
      ${dias
        .map((d) => {
          const alto = Math.max(4, Math.round((d.focos / maximo) * 100));
          const activo = seleccionado === d.dia;
          return `
            <button
              type="button"
              class="evolutivo__barra"
              data-dia="${d.dia}"
              data-parcial="${d.parcial}"
              aria-pressed="${activo}"
              title="${numero(d.focos)} focos · ${numero(d.hectareas)} ha estimadas${
                d.parcial ? ' · día en curso, incompleto' : ''
              }">
              <span class="evolutivo__cifra">${numero(d.focos)}</span>
              <span class="evolutivo__columna" style="height:${alto}%"></span>
              <span class="evolutivo__dia">${d.etiqueta}</span>
            </button>`;
        })
        .join('')}
    </div>
    <p class="evolutivo__pie">
      Focos detectados por día.
      ${
        hayParcial
          ? 'La última barra es del día en curso y está <b>incompleta</b>: ' +
            'los satélites no han terminado sus pasadas.'
          : ''
      }
    </p>`;

  for (const boton of nodo.querySelectorAll<HTMLButtonElement>('.evolutivo__barra')) {
    boton.addEventListener('click', () => {
      const dia = boton.dataset.dia!;
      opciones.alElegirDia(seleccionado === dia ? null : dia);
    });
  }
}
