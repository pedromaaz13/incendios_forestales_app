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
  fraseViento,
  resolucionSensor,
  sensores,
} from '../formato';
import { evolutivoDeIncendio } from './evolutivo';
import type { PropiedadesHotspot, PropiedadesIncidente } from '../tipos';

/**
 * Focos del incendio abierto. Los inyecta `main.ts` al cargar los datos, en
 * lugar de que la ficha lea el GeoJSON por su cuenta: así la ficha sigue siendo
 * una función de sus propiedades y se puede probar sin red.
 */
let focosPorIncendio: Map<string, Array<Pick<PropiedadesHotspot, 'acq_dt'>>> = new Map();

export function registrarFocos(
  porIncendio: Map<string, Array<Pick<PropiedadesHotspot, 'acq_dt'>>>,
): void {
  focosPorIncendio = porIncendio;
}

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
  ficha.scrollTop = 0;

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
      ${etiquetaEstado(p.status, p.ultima_observacion_h)}
    </p>
    ${dato('Confirmado por', listaFuentes(p.confirmed_by))}

    ${bloqueContexto(p)}

    ${bloqueOficial(p)}
    ${bloqueSatelital(p)}
    ${evolutivoDeIncendio(focosPorIncendio.get(p.id) ?? [])}

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

/**
 * Condiciones alrededor del incendio: viento, aviso oficial y accesos cortados.
 *
 * Va antes que los detalles del incendio porque responde la pregunta con la que
 * se entra —*¿viene hacia mí?*— y no la pregunta con la que se sale.
 *
 * Cada línea es un dato observado o declarado por otro. **No hay ninguna
 * predicción**: se dice hacia dónde sopla el viento ahora, no hacia dónde
 * avanzará el fuego. Combinar las tres cosas para pronosticar sería una opinión
 * nuestra, y este proyecto no tiene autoridad para dársela a alguien asustado.
 */
function bloqueContexto(p: PropiedadesIncidente): string {
  const viento = fraseViento(p);
  const aviso = p.aviso_nivel
    ? `<p class="ficha__dato"><span>Aviso de AEMET</span><b class="ficha__aviso ficha__aviso--${p.aviso_nivel}">${p.aviso_nivel} · ${texto(p.aviso_fenomeno)}</b></p>`
    : '';

  // Cero cortes es un dato: se miró y no había. Nulo es que no se pudo mirar,
  // y entonces no se dice nada en vez de afirmar que no hay ninguno.
  const cortes =
    p.cortes_cerca !== null && p.cortes_cerca > 0
      ? `<p class="ficha__dato"><span>Carreteras cortadas a menos de 15 km</span><b>${numero(p.cortes_cerca)}</b></p>${
          // Cuáles, no solo cuántas: «2 cortes cerca» no responde a la pregunta
          // de quien vive al lado. Los declarados por incendio van primero.
          p.cortes_vias ? `<p class="ficha__nota ficha__vias">${p.cortes_vias}</p>` : ''
        }${
          p.cortes_cerca_por_incendio
            ? `<p class="ficha__nota">${numero(p.cortes_cerca_por_incendio)} de ellas las declara la DGT causadas por incendio forestal.</p>`
            : ''
        }`
      : '';

  const ambiente =
    p.temp_c !== null
      ? `<p class="ficha__dato"><span>Temperatura</span><b>${numero(p.temp_c, 1)} ºC${
          p.humedad_pct !== null ? ` · ${numero(p.humedad_pct)} % humedad` : ''
        }</b></p>`
      : '';

  const poblacion =
    p.nucleo_cercano && p.nucleo_cercano_km !== null
      ? `<p class="ficha__dato"><span>Núcleo habitado más cercano</span><b>${texto(
          p.nucleo_cercano,
        )} · ${numero(p.nucleo_cercano_km, 1)} km</b></p>${
          p.nucleo_cercano_habitantes
            ? `<p class="ficha__nota">${numero(
                p.nucleo_cercano_habitantes,
              )} habitantes. Distancia al centro del núcleo, no a la primera casa.</p>`
            : ''
        }`
      : '';

  const terreno = p.suelo_clase
    ? `<p class="ficha__dato"><span>Terreno</span><b>${texto(p.suelo_clase)}</b></p>${
        p.suelo_tipo && p.suelo_tipo !== 'forestal'
          ? `<p class="ficha__nota">Superficie ${texto(p.suelo_tipo)}: puede tratarse de
               una quema agrícola o de una detección sobre suelo no forestal.</p>`
          : ''
      }`
    : '';

  if (!viento && !aviso && !cortes && !ambiente && !poblacion && !terreno) return '';

  return `
    <div class="ficha__seccion">
      <h3>Condiciones en la zona</h3>
      ${poblacion}
      ${terreno}
      ${viento ? `<p class="ficha__dato"><span>Viento</span><b>${texto(viento)}</b></p>` : ''}
      ${ambiente}
      ${aviso}
      ${
        p.aviso_titular
          ? `<p class="ficha__nota">${texto(p.aviso_titular)}</p>`
          : ''
      }
      ${cortes}
      <p class="ficha__estimacion">
        Viento y temperatura son la <b>observación más reciente</b> interpolada a
        esta posición, no una previsión. El aviso lo declara <b>AEMET</b> sobre la
        comarca, no sobre este incendio.
      </p>
    </div>`;
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
      ${
        p.detalle_oficial
          ? `<p class="ficha__dato ficha__dato--bloque"><span>Dónde</span></p>
             <p class="ficha__nota ficha__nota--cita">${texto(p.detalle_oficial)}</p>`
          : ''
      }
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
      ${dato('Fuente', texto(sensores(p.sensors)))}
      ${dato('Focos detectados', numero(p.n_hotspots))}
      ${dato('FRP acumulado', `${numero(p.frp_total_mw, 1)} MW`)}
      ${dato('Primera detección', fechaHora(p.first_detected))}
      ${dato('Última detección', fechaHora(p.last_detected))}
      ${
        p.focos_recientes !== null && p.focos_recientes !== undefined
          ? `${dato('Focos nuevos (últimas 6 h)', numero(p.focos_recientes))}
             ${
               p.focos_recientes > 0
                 ? `<p class="ficha__nota">
                      Equivale a unas ${numero(p.crecimiento_ha_h, 1)} ha nuevas
                      por hora <b>ya detectadas</b>. No es una previsión de lo que
                      crecerá.
                    </p>`
                 : `<p class="ficha__nota">
                      Sin focos nuevos en las últimas 6 h. Puede estar apagándose,
                      bajo nubes, o sin pasada de satélite reciente: la ausencia
                      de detección no confirma que se haya extinguido.
                    </p>`
             }`
          : ''
      }
      <p class="ficha__pista">
        Acerca el mapa para ver los ${numero(p.n_hotspots)} focos por separado.
      </p>
      ${
        p.area_est_ha !== null
          ? `
        ${dato('Superficie estimada', `${numero(p.area_est_ha)} ha`)}
        ${
          p.radio_est_km !== null && p.radio_est_km !== undefined
            ? dato('Equivale a un radio de', `${numero(p.radio_est_km, 1)} km`)
            : ''
        }
        <p class="ficha__estimacion">
          <strong>Superficie estimada</strong>, no medida. Se deriva del número
          de píxeles con anomalía térmica${
            resolucionSensor(p.sensors)
              ? `, de ${resolucionSensor(p.sensors)} de lado`
              : ''
          } y es una aproximación por defecto: el área real puede ser mayor.
        </p>`
          : ''
      }
    </div>`;
}
