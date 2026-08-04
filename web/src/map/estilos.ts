/**
 * Estilos de mapa base · RF-F-01.
 *
 * Se definen inline con teselas raster en lugar de apuntar a un servicio de
 * estilos vectoriales porque ninguno de los gratuitos funciona sin clave, y
 * meter una clave de API en un bundle público es regalarla. Raster + OSM no
 * necesita registro y su licencia (ODbL) solo exige atribución, que se muestra.
 *
 * El estilo por defecto es el claro: el visor se consulta de día, que es cuando
 * arde, y sobre fondo claro la rampa de brasa destaca más.
 */

import type { StyleSpecification } from 'maplibre-gl';

export type ClaveEstilo = 'sobrio' | 'normal' | 'satelite' | 'relieve' | 'oscuro';

/**
 * El sobrio es el defecto, y el motivo es de lectura, no de gusto.
 *
 * OSM estándar pinta las carreteras principales en naranja y rojo — los mismos
 * tonos que nuestra rampa de fuego y que las líneas de alta tensión. Sobre él,
 * un incendio y una autovía se parecen. Medido el 04-08-2026 con las capas de
 * infraestructura activas: no se distinguían.
 *
 * Un fondo gris deja el color para el dato. Es lo que hacen los visores
 * meteorológicos y los paneles operativos, y por la misma razón.
 */
export const ESTILO_POR_DEFECTO: ClaveEstilo = 'sobrio';

const ATRIBUCION_OSM =
  '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>';

const ATRIBUCION_CARTO = `${ATRIBUCION_OSM} · © <a href="https://carto.com/attributions">CARTO</a>`;

function estiloRaster(
  teselas: string[],
  atribucion: string,
  maxzoom = 19,
): StyleSpecification {
  return {
    version: 8,
    sources: {
      base: {
        type: 'raster',
        tiles: teselas,
        tileSize: 256,
        maxzoom,
        attribution: atribucion,
      },
    },
    layers: [
      { id: 'fondo', type: 'background', paint: { 'background-color': '#0f1619' } },
      { id: 'base', type: 'raster', source: 'base' },
    ],
  };
}

export const ESTILOS: Record<ClaveEstilo, { nombre: string; estilo: StyleSpecification }> = {
  sobrio: {
    nombre: 'Sobrio',
    estilo: estiloRaster(
      ['https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png'],
      ATRIBUCION_CARTO,
      20,
    ),
  },
  normal: {
    nombre: 'Normal',
    estilo: estiloRaster(
      ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
      ATRIBUCION_OSM,
    ),
  },
  satelite: {
    nombre: 'Satélite',
    estilo: estiloRaster(
      [
        'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
      ],
      'Imágenes © Esri, Maxar, Earthstar Geographics',
      18,
    ),
  },
  relieve: {
    nombre: 'Relieve',
    estilo: estiloRaster(
      ['https://tile.opentopomap.org/{z}/{x}/{y}.png'],
      `${ATRIBUCION_OSM} · <a href="https://opentopomap.org">OpenTopoMap</a> (CC-BY-SA)`,
      17,
    ),
  },
  oscuro: {
    nombre: 'Oscuro',
    estilo: estiloRaster(
      ['https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png'],
      ATRIBUCION_CARTO,
      20,
    ),
  },
};

const CLAVES = Object.keys(ESTILOS) as ClaveEstilo[];

export function esClaveEstilo(valor: string | null): valor is ClaveEstilo {
  // Se deriva del propio registro: la versión anterior enumeraba las claves a
  // mano y un estilo nuevo se quedaba fuera en silencio, de modo que la
  // preferencia guardada del usuario se descartaba sin avisar.
  return valor !== null && (CLAVES as string[]).includes(valor);
}
