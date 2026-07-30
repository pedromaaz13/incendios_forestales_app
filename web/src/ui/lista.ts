/**
 * Lista de incendios visibles · RF-F-07.
 *
 * Sincronizada con el viewport en `moveend`. El orden es por gravedad —activos
 * primero, luego por FRP descendente— y no por proximidad ni por nombre: quien
 * abre esto quiere ver antes lo que está ardiendo con más fuerza.
 */

import {
  coordenadas,
  etiquetaEstado,
  fechaHora,
  listaFuentes,
  margenPosicion,
  numero,
  siglasFuente,
} from '../formato';
import type { PropiedadesIncidente } from '../tipos';

const ORDEN_ESTADO: Record<string, number> = {
  activo: 0,
  estabilizado: 1,
  controlado: 2,
  extinguido: 3,
};

/** Por encima de este número se recorta el DOM en lugar de pintarlo entero. */
const MAXIMO_PINTADO = 100;

function texto(valor: string | null | undefined): string {
  const d = document.createElement('div');
  d.textContent = valor ?? '';
  return d.innerHTML;
}

/**
 * Orden de la lista.
 *
 * Los que tienen parte oficial van primero, y entre ellos manda la gravedad
 * declarada. El resto se ordena por **observación más reciente**: sin estado
 * declarado, lo que distingue un incendio de otro es cuánto hace que se vio
 * calor, y un fuego visto hace 40 minutos importa más que uno visto hace 20 h.
 *
 * Antes todos empataban, porque los 79 publicaban `activo` y el orden acababa
 * decidiéndolo el FRP — que mide calor, no urgencia.
 */
export function ordenarPorGravedad(
  incidentes: PropiedadesIncidente[],
): PropiedadesIncidente[] {
  return [...incidentes].sort((a, b) => {
    const oficial = Number(!!b.status) - Number(!!a.status);
    if (oficial !== 0) return oficial;

    if (a.status && b.status) {
      const estado = (ORDEN_ESTADO[a.status] ?? 9) - (ORDEN_ESTADO[b.status] ?? 9);
      if (estado !== 0) return estado;
    } else {
      const visto = (a.ultima_observacion_h ?? 1e9) - (b.ultima_observacion_h ?? 1e9);
      if (visto !== 0) return visto;
    }

    return (b.frp_total_mw ?? 0) - (a.frp_total_mw ?? 0);
  });
}

export interface ManejadoresLista {
  alPulsar: (id: string) => void;
  alEntrar: (id: string) => void;
  alSalir: () => void;
}

export function pintarLista(
  incidentes: PropiedadesIncidente[],
  manejadores: ManejadoresLista,
): void {
  const lista = document.getElementById('lista-incidentes')!;
  const contador = document.getElementById('contador-visibles')!;

  contador.textContent = String(incidentes.length);

  // RF-F-13: el viewport sin incendios dice algo, no se queda en blanco. Un
  // panel vacío se lee como "no ha cargado", no como "no hay nada".
  if (incidentes.length === 0) {
    lista.innerHTML =
      '<li class="vacio">Sin incendios detectados en esta zona del mapa.<br>' +
      'Amplía la vista o desplázate para ver otras zonas.</li>';
    return;
  }

  const ordenados = ordenarPorGravedad(incidentes);
  const visibles = ordenados.slice(0, MAXIMO_PINTADO);

  lista.innerHTML =
    visibles.map(tarjeta).join('') +
    (ordenados.length > MAXIMO_PINTADO
      ? `<li class="vacio">y ${numero(ordenados.length - MAXIMO_PINTADO)} más. Amplía el zoom para acotar.</li>`
      : '');

  for (const boton of lista.querySelectorAll<HTMLButtonElement>('.tarjeta')) {
    const id = boton.dataset.id!;
    boton.addEventListener('click', () => manejadores.alPulsar(id));
    boton.addEventListener('mouseenter', () => manejadores.alEntrar(id));
    boton.addEventListener('focus', () => manejadores.alEntrar(id));
    boton.addEventListener('mouseleave', manejadores.alSalir);
    boton.addEventListener('blur', manejadores.alSalir);
  }
}

function tarjeta(p: PropiedadesIncidente): string {
  const siglas = siglasFuente(p.confirmed_by);
  // Sin capa municipal no hay nombre, pero sí coordenadas: decir dónde está es
  // lo que la persona ha venido a saber.
  const hayCoordenadas = p._lon !== undefined && p._lat !== undefined;
  const lugar =
    p.municipio ?? (hayCoordenadas ? coordenadas(p._lon!, p._lat!) : 'Ubicación por determinar');
  const provincia = p.provincia ? `, ${texto(p.provincia)}` : '';

  const medios = p.resources_text
    ? `<div>Medios: ${texto(p.resources_text)}</div>`
    : mediosDesglosados(p);

  const igr = p.igr_level !== null ? `<div>Nivel IGR: <b>${p.igr_level}</b></div>` : '';

  // Se etiqueta el origen sin verbos de certeza: "detectado por satélite", no
  // "hay un incendio".
  const origen =
    p.origin === 'ambos'
      ? `Satélite y ${listaFuentes(p.confirmed_by)}`
      : p.origin === 'oficial'
        ? `Parte de ${listaFuentes(p.confirmed_by)} · sin detección satelital`
        : 'Detección satelital sin parte oficial';

  return `
    <li>
      <button type="button" class="tarjeta" data-id="${texto(p.id)}">
        <span class="tarjeta__cabecera">
          ${siglas ? `<span class="distintivo">${texto(siglas)}</span>` : ''}
          <span class="tarjeta__lugar">${texto(lugar)}${provincia}</span>
        </span>
        <span class="tarjeta__meta">
          <span class="tarjeta__estado" data-estado="${texto(p.status ?? 'sin-declarar')}">
            ${etiquetaEstado(p.status, p.ultima_observacion_h)}
          </span>
          ${igr}
          ${medios}
          <div>${origen}</div>
          <div>Última detección: ${fechaHora(p.last_detected)} · ${margenPosicion(
            p.position_precision_m,
          )}</div>
        </span>
      </button>
    </li>`;
}

function mediosDesglosados(p: PropiedadesIncidente): string {
  const partes: string[] = [];
  if (p.resources_air) partes.push(`${p.resources_air} aéreos`);
  if (p.resources_ground) partes.push(`${p.resources_ground} terrestres`);
  if (p.resources_people) partes.push(`${p.resources_people} personas`);
  return partes.length ? `<div>Medios: ${partes.join(' · ')}</div>` : '';
}
