/**
 * Capas de datos · RF-F-03, RF-F-04, RF-F-11.
 *
 * Toda la codificación visual va en expresiones de MapLibre, no en JavaScript.
 * Es lo que permite que un cambio de filtro sea `setFilter` sobre la GPU y
 * cumpla los 100 ms de RNF-04 con 50.000 features: reconstruir marcadores en JS
 * no llegaría ni de lejos.
 */

import type { ExpressionSpecification, Map as MapaGL } from 'maplibre-gl';

export const FUENTE_INCIDENTES = 'incidentes';
export const FUENTE_HOTSPOTS = 'hotspots';
export const FUENTE_PERIMETROS = 'perimetros';
export const FUENTE_VIENTO = 'viento';
export const FUENTE_AIRE = 'aire';

export const CAPA_INCERTIDUMBRE = 'incidentes-incertidumbre';
export const CAPA_INCIDENTES = 'incidentes-simbolo';
export const CAPA_RESALTE = 'incidentes-resalte';
export const CAPA_HOTSPOTS = 'hotspots-punto';
export const CAPA_PERIMETRO_ESTIMADO = 'perimetros-estimado';
export const CAPA_PERIMETRO_EFFIS = 'perimetros-effis';
export const CAPA_VIENTO = 'viento-flecha';
export const CAPA_AIRE = 'aire-circulo';

/** Zoom a partir del cual aparecen los hotspots crudos (RF-F-04). */
export const ZOOM_HOTSPOTS = 9;

/** Rampa de brasa. Cada parada es un umbral de FRP, no una elección estética. */
const COLOR_POR_INTENSIDAD: ExpressionSpecification = [
  'match',
  ['get', 'intensity'],
  'baja', '#ffe08a',
  'media', '#ffa23a',
  'alta', '#f05a28',
  'extrema', '#c81e1e',
  '#ffa23a',
];

/**
 * Radio del anillo de incertidumbre, en píxeles reales sobre el terreno.
 *
 * `_radio_base` lo calcula `datos.ts`: metros / (metros-por-píxel a zoom 0).
 * La interpolación exponencial de base 2 reproduce el factor 2^zoom de Web
 * Mercator, así que el anillo mantiene su tamaño **métrico** a cualquier zoom.
 *
 * Esta capa es la diferencia central del producto (RF-F-03). Un incidente de
 * INFOCAM con ±6 km se dibuja como un anillo de 6 km; pintarlo como un punto
 * fingiría una precisión que la fuente no tiene, y alguien podría creer que el
 * fuego está exactamente ahí.
 */
const RADIO_INCERTIDUMBRE: ExpressionSpecification = [
  'interpolate',
  ['exponential', 2],
  ['zoom'],
  0, ['get', '_radio_base'],
  22, ['*', ['get', '_radio_base'], 4194304], // 2^22
];

/** Radio del símbolo, por número de hotspots e interpolado por zoom. */
const RADIO_SIMBOLO: ExpressionSpecification = [
  'interpolate',
  ['linear'],
  ['zoom'],
  4, ['interpolate', ['linear'], ['get', 'n_hotspots'], 0, 3.5, 10, 5, 100, 9],
  9, ['interpolate', ['linear'], ['get', 'n_hotspots'], 0, 6, 10, 10, 100, 20],
  14, ['interpolate', ['linear'], ['get', 'n_hotspots'], 0, 10, 10, 16, 100, 34],
];

/** Activo opaco, controlado translúcido: la opacidad codifica el estado. */
const OPACIDAD_POR_ESTADO: ExpressionSpecification = [
  'match',
  ['get', 'status'],
  'activo', 0.9,
  'estabilizado', 0.72,
  'controlado', 0.5,
  0.62,
];

export function anadirCapasIncidentes(mapa: MapaGL): void {
  // El anillo va debajo del símbolo para que no lo tape.
  mapa.addLayer({
    id: CAPA_INCERTIDUMBRE,
    type: 'circle',
    source: FUENTE_INCIDENTES,
    paint: {
      'circle-radius': RADIO_INCERTIDUMBRE,
      'circle-color': COLOR_POR_INTENSIDAD,
      'circle-opacity': 0.07,
      'circle-stroke-width': 1,
      'circle-stroke-color': COLOR_POR_INTENSIDAD,
      'circle-stroke-opacity': 0.45,
    },
  });

  mapa.addLayer({
    id: CAPA_RESALTE,
    type: 'circle',
    source: FUENTE_INCIDENTES,
    filter: ['==', ['get', 'id'], ''],
    paint: {
      'circle-radius': ['+', RADIO_SIMBOLO, 7],
      'circle-color': 'transparent',
      'circle-stroke-width': 2,
      'circle-stroke-color': '#ffffff',
      'circle-stroke-opacity': 0.9,
    },
  });

  mapa.addLayer({
    id: CAPA_INCIDENTES,
    type: 'circle',
    source: FUENTE_INCIDENTES,
    paint: {
      'circle-radius': RADIO_SIMBOLO,
      'circle-color': COLOR_POR_INTENSIDAD,
      'circle-opacity': OPACIDAD_POR_ESTADO,
      // El borde grueso marca confirmación oficial: es la diferencia entre
      // "el satélite ha visto calor" y "hay bomberos trabajando ahí".
      'circle-stroke-width': ['case', ['get', 'official_confirmed'], 2.5, 1],
      'circle-stroke-color': ['case', ['get', 'official_confirmed'], '#ffffff', '#7d9199'],
      'circle-stroke-opacity': 0.85,
    },
  });
}

/**
 * Hotspots crudos, solo desde zoom 9.
 *
 * Sin clustering numérico, y esto es una prohibición explícita de RF-F-04: un
 * badge con "638" mezcla incendios reales, quemas agrícolas y falsos positivos
 * en un número que parece una magnitud y no lo es. A zoom bajo se muestran
 * incidentes; los hotspots son detalle de zoom alto.
 */
export function anadirCapaHotspots(mapa: MapaGL): void {
  mapa.addLayer(
    {
      id: CAPA_HOTSPOTS,
      type: 'circle',
      source: FUENTE_HOTSPOTS,
      minzoom: ZOOM_HOTSPOTS,
      paint: {
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 9, 1.8, 14, 4.5],
        'circle-color': [
          'interpolate',
          ['linear'],
          ['get', 'frp_mw'],
          0, '#ffe08a',
          20, '#ffa23a',
          80, '#f05a28',
          200, '#c81e1e',
        ],
        'circle-opacity': 0.55,
        'circle-stroke-width': 0,
      },
    },
    CAPA_INCERTIDUMBRE,
  );
}

export function anadirCapasPerimetros(mapa: MapaGL): void {
  // EFFIS: cartografiado, fiable, trazo sólido.
  mapa.addLayer(
    {
      id: CAPA_PERIMETRO_EFFIS,
      type: 'line',
      source: FUENTE_PERIMETROS,
      filter: ['!=', ['get', 'is_estimate'], true],
      paint: { 'line-color': '#c81e1e', 'line-width': 2, 'line-opacity': 0.85 },
    },
    CAPA_INCERTIDUMBRE,
  );

  // Estimado desde el hull de los hotspots: trazo punteado. La distinción
  // visual es obligatoria (RF-P-08) porque una estimación dibujada igual que
  // una medición se lee como una medición.
  mapa.addLayer(
    {
      id: CAPA_PERIMETRO_ESTIMADO,
      type: 'line',
      source: FUENTE_PERIMETROS,
      filter: ['==', ['get', 'is_estimate'], true],
      paint: {
        'line-color': '#ffa23a',
        'line-width': 1.5,
        'line-opacity': 0.8,
        'line-dasharray': [2, 2],
      },
    },
    CAPA_INCERTIDUMBRE,
  );
}

/**
 * Viento. La flecha apunta a `direction_to_deg`, es decir **hacia dónde sopla**.
 *
 * La convención meteorológica es la contraria —el ángulo indica de dónde viene—
 * y confunde a cualquiera que no sea meteorólogo. Aquí la lectura errónea es
 * cara: significa creer que el frente avanza al revés. La leyenda lo explica.
 */
export const ICONO_FLECHA = 'flecha-viento';

/**
 * Dibuja la flecha del viento en un lienzo y la registra como imagen.
 *
 * No se usa un símbolo de texto porque los estilos raster de `estilos.ts` no
 * declaran `glyphs`, y sin servidor de fuentes MapLibre no pinta `text-field`:
 * la capa se activaba y no aparecía nada. Un icono generado aquí no depende de
 * ningún servicio externo, que además es una dependencia menos que se pueda
 * caer en mitad de un incendio.
 */
function registrarIconoFlecha(mapa: MapaGL): void {
  if (mapa.hasImage(ICONO_FLECHA)) return;

  const lado = 64;
  const lienzo = document.createElement('canvas');
  lienzo.width = lado;
  lienzo.height = lado;
  const ctx = lienzo.getContext('2d');
  if (!ctx) return;

  // Flecha apuntando hacia arriba; `icon-rotate` la orienta después. El origen
  // del ángulo en MapLibre es el norte y crece en sentido horario, que es la
  // misma convención en la que viene `direction_to_deg`.
  ctx.translate(lado / 2, lado / 2);
  ctx.beginPath();
  ctx.moveTo(0, -26);
  ctx.lineTo(13, 10);
  ctx.lineTo(0, 2);
  ctx.lineTo(-13, 10);
  ctx.closePath();

  ctx.fillStyle = '#ffffff';
  ctx.fill();
  ctx.lineWidth = 3;
  ctx.strokeStyle = 'rgba(15,22,25,0.85)';
  ctx.stroke();

  const datos = ctx.getImageData(0, 0, lado, lado);
  // `sdf: true` permite recolorear el icono por velocidad con `icon-color`,
  // igual que se haría con un símbolo de texto.
  mapa.addImage(ICONO_FLECHA, datos, { sdf: true });
}

export function anadirCapaViento(mapa: MapaGL): void {
  registrarIconoFlecha(mapa);

  mapa.addLayer({
    id: CAPA_VIENTO,
    type: 'symbol',
    source: FUENTE_VIENTO,
    layout: {
      'icon-image': ICONO_FLECHA,
      'icon-size': ['interpolate', ['linear'], ['get', 'speed_kmh'], 0, 0.35, 60, 0.75],
      'icon-rotate': ['get', 'direction_to_deg'],
      'icon-rotation-alignment': 'map',
      'icon-allow-overlap': true,
      'icon-ignore-placement': true,
    },
    paint: {
      'icon-color': [
        'step',
        ['get', 'speed_kmh'],
        '#4ac97e', 15,
        '#ffe08a', 30,
        '#ffa23a', 50,
        '#c81e1e',
      ],
      'icon-halo-color': 'rgba(15,22,25,0.9)',
      'icon-halo-width': 1.5,
    },
  });
}


/**
 * Calidad del aire · índice europeo (EAQI).
 *
 * Los cortes de color son los que publica la Agencia Europea de Medio Ambiente,
 * no una rampa continua: el EAQI no es un porcentaje y sus tramos marcan
 * recomendaciones sanitarias distintas. Interpolar entre ellos difuminaría justo
 * la frontera donde cambia el consejo.
 *
 * Va por debajo de los incendios a propósito. Es contexto —el humo llega mucho
 * más lejos que el fuego— pero no debe tapar el dato principal, y sobre todo no
 * insinúa causalidad: un índice alto puede ser tráfico, calima sahariana o un
 * incendio a 200 km, y la capa no distingue cuál.
 */
export function anadirCapaAire(mapa: MapaGL): void {
  mapa.addLayer(
    {
      id: CAPA_AIRE,
      type: 'circle',
      source: FUENTE_AIRE,
      paint: {
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 4, 9, 10, 26],
        'circle-color': [
          'step',
          ['get', 'aqi'],
          '#4ac97e', 20,
          '#c8d94a', 40,
          '#ffe08a', 60,
          '#ffa23a', 80,
          '#f05a28', 100,
          '#8b2fa8',
        ],
        'circle-opacity': 0.35,
        'circle-stroke-width': 1,
        'circle-stroke-color': [
          'step',
          ['get', 'aqi'],
          '#4ac97e', 20,
          '#c8d94a', 40,
          '#ffe08a', 60,
          '#ffa23a', 80,
          '#f05a28', 100,
          '#8b2fa8',
        ],
        'circle-stroke-opacity': 0.75,
      },
    },
    CAPA_INCERTIDUMBRE,
  );
}
