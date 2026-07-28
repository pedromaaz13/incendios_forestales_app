/**
 * Ficha de incidente · RF-F-10.
 *
 * Cinco bloques en el orden que fija la especificación. Dos reglas de dominio
 * mandan sobre la estética:
 *
 *  - La superficie lleva **siempre** la palabra "estimada" visible, en un aviso
 *    destacado y no en letra pequeña. Es una cota inferior grosera derivada del
 *    número de píxeles, no una medición, y presentarla como un área quemada
 *    real sería el error que este visor existe para no cometer.
 *  - El margen de posición se enuncia en lenguaje llano ("±6 km según la
 *    fuente"), porque el anillo del mapa no se entiende sin el texto.
 */

import {
  coordenadas,
  etiquetaEstado,
  fechaHora,
  listaFuentes,
  margenPosicion,
  numero,
} from '../formato';
import type { PropiedadesIncidente } from '../tipos';

function texto(valor: string | null | undefined): string {
  const d = document.createElement('div');
  d.textContent = valor ?? '';
  return d.innerHTML;
}

function dato(etiqueta: string, valor: string): string {
  return `<div class="ficha__dato"><span>${etiqueta}</span><b>${valor}</b></div>`;
}

export function abrirFicha(p: PropiedadesIncidente, alCerrar: () => void): void {
  const ficha = document.getElementById('ficha')!;
  ficha.hidden = false;
  ficha.innerHTML = contenido(p);

  const cerrar = ficha.querySelector<HTMLButtonElement>('.ficha__cerrar')!;
  cerrar.addEventListener('click', alCerrar);
  cerrar.focus();
}

export function cerrarFicha(): void {
  const ficha = document.getElementById('ficha')!;
  ficha.hidden = true;
  ficha.innerHTML = '';
}

function contenido(p: PropiedadesIncidente): string {
  const enlace = `${location.pathname}?id=${encodeURIComponent(p.id)}`;

  return `
    <button type="button" class="ficha__cerrar" aria-label="Cerrar ficha">×</button>

    <h2 id="ficha-titulo">${texto(
      p.municipio ??
        (p._lon !== undefined && p._lat !== undefined
          ? coordenadas(p._lon, p._lat)
          : 'Ubicación por determinar'),
    )}</h2>
    <p class="ficha__provincia">
      ${texto(p.provincia ?? (p.municipio ? 'Provincia no facilitada' : 'Sin nombre de municipio disponible'))} ·
      ${etiquetaEstado(p.status)}
    </p>
    ${dato('Confirmado por', listaFuentes(p.confirmed_by))}

    ${bloqueOficial(p)}
    ${bloqueSatelital(p)}

    <div class="ficha__seccion">
      <h3>Precisión de la posición</h3>
      <p class="ficha__dato"><span>Margen declarado</span><b>${margenPosicion(
        p.position_precision_m,
      )}</b></p>
      <p class="ficha__estimacion">
        La posición tiene un margen de ${margenPosicion(p.position_precision_m)}
        según la fuente que la publica. El anillo punteado del mapa representa
        ese margen real: el incendio puede estar en cualquier punto de su interior.
      </p>
    </div>

    <a class="ficha__enlace" href="${enlace}">Enlace permanente a este incidente</a>`;
}

function bloqueOficial(p: PropiedadesIncidente): string {
  if (!p.official_confirmed) return '';

  const medios = p.resources_text
    ? dato('Medios', texto(p.resources_text))
    : [
        p.resources_air !== null ? dato('Medios aéreos', numero(p.resources_air)) : '',
        p.resources_ground !== null ? dato('Medios terrestres', numero(p.resources_ground)) : '',
        p.resources_people !== null ? dato('Personas', numero(p.resources_people)) : '',
      ].join('');

  return `
    <div class="ficha__seccion">
      <h3>Parte oficial</h3>
      ${p.igr_level !== null ? dato('Nivel IGR', String(p.igr_level)) : ''}
      ${medios}
      ${p.started_at ? dato('Inicio declarado', fechaHora(p.started_at)) : ''}
    </div>`;
}

function bloqueSatelital(p: PropiedadesIncidente): string {
  if (!p.satellite_confirmed) {
    return `
      <div class="ficha__seccion">
        <h3>Detección satelital</h3>
        <p class="ficha__estimacion">
          Sin focos de calor asociados. Puede ser un incendio pequeño, o estar
          bajo nubes o fuera de la última pasada de los satélites. La ausencia de
          detección no significa que no exista.
        </p>
      </div>`;
  }

  return `
    <div class="ficha__seccion">
      <h3>Detección satelital</h3>
      ${dato('Focos detectados', numero(p.n_hotspots))}
      ${dato('FRP acumulado', `${numero(p.frp_total_mw, 1)} MW`)}
      ${dato('Primera detección', fechaHora(p.first_detected))}
      ${dato('Última detección', fechaHora(p.last_detected))}
      ${
        p.area_est_ha !== null
          ? `
        ${dato('Superficie estimada', `${numero(p.area_est_ha)} ha`)}
        <p class="ficha__estimacion">
          <strong>Superficie estimada</strong>, no medida. Se deriva del número
          de píxeles con anomalía térmica y es una aproximación por defecto: el
          área real puede ser mayor.
        </p>`
          : ''
      }
    </div>`;
}
