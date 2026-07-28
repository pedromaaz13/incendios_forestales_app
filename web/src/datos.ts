/**
 * Carga de los artefactos estáticos de `live/`.
 *
 * No hay backend ni base de datos: son ficheros en CDN (sección 2.2). Cada
 * carga puede fallar de forma independiente y el visor tiene que seguir siendo
 * navegable, así que ninguna función de aquí lanza: devuelven `null` y quien
 * llama decide qué banda pintar.
 *
 * `manifest.json` se pide con `cache: 'no-cache'` a propósito. Es lo único que
 * revalida siempre: si el navegador sirviera un manifiesto cacheado, la
 * latencia publicada sería mentira, y publicar la latencia correcta es el
 * motivo de existir de este proyecto.
 */

import type { ColeccionIncidentes, Manifiesto, Salud } from './tipos';

// `BASE_URL` lo inyecta Vite: '/' en local y '/nombre-del-repo/' en GitHub
// Pages. Codificar '/live' a pelo rompería el despliegue bajo subruta con
// cuatro 404 silenciosos, que es justo el fallo que este proyecto vigila.
const BASE = `${import.meta.env.BASE_URL.replace(/\/$/, '')}/live`;

export class ErrorDatos extends Error {
  constructor(
    public readonly recurso: string,
    causa: unknown,
  ) {
    super(`No se ha podido cargar ${recurso}: ${causa}`);
    this.name = 'ErrorDatos';
  }
}

async function pedirJson<T>(ruta: string, revalidar = false): Promise<T> {
  const respuesta = await fetch(`${BASE}/${ruta}`, {
    cache: revalidar ? 'no-cache' : 'default',
  });
  if (!respuesta.ok) {
    throw new ErrorDatos(ruta, `HTTP ${respuesta.status}`);
  }
  return (await respuesta.json()) as T;
}

/**
 * El manifiesto es el único recurso obligatorio: sin él no se puede afirmar
 * nada sobre la antigüedad de lo que se muestra, y mostrar incendios sin poder
 * decir de cuándo son es justo lo que RF-F-13 prohíbe.
 */
export async function cargarManifiesto(): Promise<Manifiesto> {
  const m = await pedirJson<Manifiesto>('manifest.json', true);
  if (typeof m?.schema_version !== 'number' || !m?.counts) {
    throw new ErrorDatos('manifest.json', 'esquema no reconocido');
  }
  return m;
}

export async function cargarSalud(): Promise<Salud | null> {
  try {
    return await pedirJson<Salud>('sources.json', true);
  } catch {
    // El panel de fuentes es informativo: su ausencia no justifica dejar el
    // mapa en blanco.
    return null;
  }
}

export async function cargarIncidentes(): Promise<ColeccionIncidentes> {
  const fc = await pedirJson<ColeccionIncidentes>('incidents.geojson');
  return prepararIncidentes(fc);
}

export async function cargarGeoJson(
  nombre: string,
): Promise<GeoJSON.FeatureCollection | null> {
  try {
    return await pedirJson<GeoJSON.FeatureCollection>(nombre);
  } catch {
    return null;
  }
}

/** Metros por píxel a zoom 0 en el ecuador, para la proyección Web Mercator. */
const METROS_POR_PIXEL_Z0 = 156543.03392;

/**
 * Precalcula el radio del anillo de incertidumbre.
 *
 * MapLibre expresa `circle-radius` en píxeles, pero `position_precision_m` está
 * en metros: dibujarlo directo daría un anillo que no cambia con el zoom y por
 * tanto miente sobre la escala. Se guarda el radio equivalente a zoom 0 y el
 * estilo lo escala con `['exponential', 2]`, que reproduce exactamente el
 * factor 2^zoom de Mercator.
 *
 * El coseno de la latitud entra aquí porque Mercator estira las distancias
 * hacia los polos: sin él, un anillo de 6 km en Cantabria saldría más pequeño
 * que uno de 6 km en Cádiz.
 */
export function prepararIncidentes(fc: ColeccionIncidentes): ColeccionIncidentes {
  for (const f of fc.features) {
    const metros = Number(f.properties.position_precision_m) || 0;
    const lat = f.geometry?.coordinates?.[1] ?? 40;
    const cos = Math.cos((lat * Math.PI) / 180) || 1;
    f.properties._radio_base = metros / (METROS_POR_PIXEL_Z0 * cos);
    f.properties._lon = f.geometry?.coordinates?.[0];
    f.properties._lat = lat;
  }
  return fc;
}
