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

export type ClaveEstilo = 'normal' | 'satelite' | 'relieve';

export const ESTILO_POR_DEFECTO: ClaveEstilo = 'normal';

const ATRIBUCION_OSM =
  '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>';

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
};

export function esClaveEstilo(valor: string | null): valor is ClaveEstilo {
  return valor === 'normal' || valor === 'satelite' || valor === 'relieve';
}
