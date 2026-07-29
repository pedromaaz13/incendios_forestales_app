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
export const FUENTE_TRAFICO = 'trafico';

export const CAPA_INCERTIDUMBRE = 'incidentes-incertidumbre';
export const CAPA_INCIDENTES = 'incidentes-simbolo';
export const CAPA_GRUPOS = 'incidentes-grupo';
export const CAPA_GRUPOS_NUM = 'incidentes-grupo-numero';
export const CAPA_RESALTE = 'incidentes-resalte';
export const CAPA_HOTSPOTS = 'hotspots-punto';
export const CAPA_PERIMETRO_ESTIMADO = 'perimetros-estimado';
export const CAPA_PERIMETRO_EFFIS = 'perimetros-effis';
export const CAPA_VIENTO = 'viento-flecha';
export const CAPA_AIRE = 'aire-circulo';
export const CAPA_TRAFICO = 'trafico-corte';
export const CAPA_TRAFICO_INCENDIO = 'trafico-corte-incendio';

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

/**
 * Agrupación numérica de incidentes · matiz sobre RF-F-04.
 *
 * La especificación prohíbe el badge numérico **de hotspots**, y con razón: un
 * "638" mezcla incendios reales, quemas agrícolas y falsos positivos en un
 * número que parece una magnitud y no lo es.
 *
 * Aquí se agrupan **incidentes**, que es otra cosa. Han pasado por el filtro de
 * confianza, la máscara industrial, la deduplicación, el clustering y los ocho
 * invariantes. Un globo que dice "3" significa tres incendios, y eso sí es una
 * afirmación que el dato sostiene. Los hotspots siguen sin agruparse nunca.
 */
export function anadirCapasGrupos(mapa: MapaGL): void {
  // El color del globo lo marca el incendio más grave que contiene, no la
  // media: si dentro hay uno extremo, el grupo se pinta como extremo. Promediar
  // escondería el que importa.
  mapa.addLayer({
    id: CAPA_GRUPOS,
    type: 'circle',
    source: FUENTE_INCIDENTES,
    filter: ['has', 'point_count'],
    paint: {
      'circle-radius': [
        'interpolate', ['linear'], ['get', 'point_count'],
        2, 15,
        10, 22,
        50, 32,
        200, 44,
      ],
      'circle-color': [
        'step', ['get', 'peor'],
        '#ffd93d', 2,
        '#ff9f1c', 3,
        '#ff5714', 4,
        '#e01e37',
      ],
      'circle-opacity': 0.9,
      'circle-stroke-width': 3,
      'circle-stroke-color': '#0d1117',
      'circle-stroke-opacity': 0.75,
    },
  });

  // El número va como icono generado y no como `text-field`. Los estilos raster
  // de `estilos.ts` no declaran `glyphs`, así que MapLibre no tiene con qué
  // dibujar texto y la etiqueta no aparecería — el mismo fallo que tuvo la capa
  // de viento. Apuntar a un servidor de fuentes externo tampoco vale: el de
  // demotiles devuelve 404 y sería otra dependencia que se cae en silencio.
  mapa.addLayer({
    id: CAPA_GRUPOS_NUM,
    type: 'symbol',
    source: FUENTE_INCIDENTES,
    filter: ['has', 'point_count'],
    layout: {
      'icon-image': ['concat', 'grupo-', ['get', 'point_count_abbreviated']],
      'icon-allow-overlap': true,
      'icon-ignore-placement': true,
    },
  });
}

/**
 * Genera las cifras de los globos a medida que hacen falta.
 *
 * `styleimagemissing` avisa cuando el estilo pide una imagen que no existe, que
 * es justo lo que pasa la primera vez que aparece un grupo de N incendios. Se
 * dibuja la cifra en un lienzo y se registra con ese nombre.
 */
export function registrarCifrasDeGrupo(mapa: MapaGL): void {
  mapa.on('styleimagemissing', (ev) => {
    const nombre = ev.id;
    if (!nombre.startsWith('grupo-') || mapa.hasImage(nombre)) return;

    const texto = nombre.slice('grupo-'.length);
    const escala = 2; // para que se vea nítido en pantallas de alta densidad
    const lado = 64 * escala;

    const lienzo = document.createElement('canvas');
    lienzo.width = lado;
    lienzo.height = lado;
    const ctx = lienzo.getContext('2d');
    if (!ctx) return;

    // Cifra oscura con halo claro: es el par de más contraste sobre el globo
    // cálido y se lee igual en el mapa normal, el de satélite y el de relieve.
    const tam = (texto.length > 3 ? 20 : texto.length > 2 ? 24 : 28) * escala;
    ctx.font = `700 ${tam}px Archivo, "Helvetica Neue", system-ui, sans-serif`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';

    ctx.lineWidth = 3 * escala;
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.55)';
    ctx.strokeText(texto, lado / 2, lado / 2);
    ctx.fillStyle = '#3d1200';
    ctx.fillText(texto, lado / 2, lado / 2);

    mapa.addImage(nombre, ctx.getImageData(0, 0, lado, lado), { pixelRatio: escala });
  });
}

export function anadirCapasIncidentes(mapa: MapaGL): void {
  // El anillo va debajo del símbolo para que no lo tape.
  mapa.addLayer({
    id: CAPA_INCERTIDUMBRE,
    type: 'circle',
    source: FUENTE_INCIDENTES,
    filter: ['!', ['has', 'point_count']],
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
    filter: ['all', ['!', ['has', 'point_count']], ['==', ['get', 'id'], '']],
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
    filter: ['!', ['has', 'point_count']],
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


/**
 * Cortes de tráfico · RF-F-11.
 *
 * Dos capas y no una. Los cortes **declarados por incendio forestal** —la DGT lo
 * dice en su propio vocabulario, aquí no se deduce— van en su capa, más grandes
 * y con halo, porque son la información más accionable del visor: quien tiene
 * que salir de una zona necesita saber por dónde no puede pasar. El resto de
 * cortes va apagado en gris, como contexto de carretera.
 *
 * Un accidente a 2 km de un foco NO se pinta como corte por incendio. La marca
 * viene del campo `por_incendio`, que refleja lo que declara la DGT.
 */
export function anadirCapasTrafico(mapa: MapaGL): void {
  mapa.addLayer({
    id: CAPA_TRAFICO,
    type: 'circle',
    source: FUENTE_TRAFICO,
    filter: ['!=', ['get', 'por_incendio'], true],
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 6, 2.5, 12, 6],
      'circle-color': '#7d9199',
      'circle-opacity': 0.7,
      'circle-stroke-width': 1,
      'circle-stroke-color': '#0f1619',
    },
  });

  mapa.addLayer({
    id: CAPA_TRAFICO_INCENDIO,
    type: 'circle',
    source: FUENTE_TRAFICO,
    filter: ['==', ['get', 'por_incendio'], true],
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 6, 5, 12, 12],
      'circle-color': '#ffe08a',
      'circle-opacity': 0.95,
      'circle-stroke-width': 2.5,
      'circle-stroke-color': '#c81e1e',
    },
  });
}


/**
 * Hace ruidosos los fallos que MapLibre reporta en voz baja.
 *
 * El caso que motivó esto nos mordió dos veces —flechas de viento y cifras de
 * los grupos— y las dos se descubrieron mirando una captura. El diagnóstico
 * inicial era erróneo: no es que la capa se pinte muda, es que **MapLibre no la
 * añade**, y lo comunica por el evento `error` sin lanzar excepción ni escribir
 * en consola:
 *
 *   layers.X.layout.text-field: use of "text-field" requires a style
 *   "glyphs" property
 *
 * Por eso escanear el estilo no servía: la capa rechazada nunca llega a estar
 * en él. Escuchar el evento sí, y de paso cubre cualquier otro fallo de estilo
 * o de fuente que MapLibre calle: peticiones de teselas fallidas, capas mal
 * formadas, expresiones inválidas.
 *
 * No se corta la aplicación en producción: un error de estilo no debe dejar sin
 * mapa a quien está mirando si arde algo cerca de su casa. Se registra, y en
 * desarrollo se lanza para que salte durante las pruebas.
 */
export function hacerRuidososLosErrores(mapa: MapaGL): void {
  mapa.on('error', (ev) => {
    const mensaje = ev.error?.message ?? String(ev);

    // Las teselas del mapa base fallan de forma esperable —sin red, tras un
    // adblocker, en las capturas de regresión— y el visor ya lo tolera. Avisar
    // de cada una ahogaría los errores que sí importan.
    if (/tile|Failed to fetch|NetworkError|AbortError/i.test(mensaje)) return;

    console.error(`[mapa] ${mensaje}`);
    (window as unknown as { __erroresMapa?: string[] }).__erroresMapa ??= [];
    (window as unknown as { __erroresMapa: string[] }).__erroresMapa.push(mensaje);
  });
}
