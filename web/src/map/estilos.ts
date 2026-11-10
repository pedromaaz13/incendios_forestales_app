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

/**
 * Fondos grises de Esri en lugar de CARTO.
 *
 * CARTO empezó a exigir clave en sus teselas raster en otoño de 2026, y sin
 * ella no da error: responde 200 con «API KEY REQUIRED» pintado en cada
 * tesela. Es el fallo silencioso que este proyecto evita en todo lo demás —el
 * visor parecía funcionar y no se veía ni una costa—. Meter la clave en el
 * bundle público sería regalarla, así que se cambia de proveedor.
 *
 * El gris de Esri sirve la base y los rótulos por separado; sin la capa de
 * rótulos no hay nombres de lugar y nadie se orienta. Los dos llegan a z16,
 * suficiente para situar un pueblo.
 */
const ESRI_CANVAS = 'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas';
const ATRIBUCION_ESRI_GRIS = `Mapa base © Esri, HERE, Garmin · ${ATRIBUCION_OSM}`;

function estiloRaster(
  teselas: string[],
  atribucion: string,
  maxzoom = 19,
  rotulos?: string[],
): StyleSpecification {
  const fuente = (tiles: string[], attribution?: string) => ({
    type: 'raster' as const,
    tiles,
    tileSize: 256,
    maxzoom,
    ...(attribution ? { attribution } : {}),
  });
  return {
    version: 8,
    sources: {
      base: fuente(teselas, atribucion),
      ...(rotulos ? { rotulos: fuente(rotulos) } : {}),
    },
    layers: [
      { id: 'fondo', type: 'background', paint: { 'background-color': '#0f1619' } },
      { id: 'base', type: 'raster', source: 'base' },
      ...(rotulos ? [{ id: 'rotulos', type: 'raster' as const, source: 'rotulos' }] : []),
    ],
  };
}

export const ESTILOS: Record<ClaveEstilo, { nombre: string; estilo: StyleSpecification }> = {
  sobrio: {
    nombre: 'Sobrio',
    estilo: estiloRaster(
      [`${ESRI_CANVAS}/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}`],
      ATRIBUCION_ESRI_GRIS,
      16,
      [`${ESRI_CANVAS}/World_Light_Gray_Reference/MapServer/tile/{z}/{y}/{x}`],
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
      [`${ESRI_CANVAS}/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}`],
      ATRIBUCION_ESRI_GRIS,
      16,
      [`${ESRI_CANVAS}/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}`],
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
