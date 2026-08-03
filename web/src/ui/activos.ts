/**
 * Mis activos · cruzar tus puntos con los incendios publicados.
 *
 * El problema que resuelve: una eléctrica tiene 200 subestaciones, un grupo
 * hotelero 40 campings, un ayuntamiento sus pedanías. Todos miran el mismo mapa
 * y ninguno puede responder «¿cuál de los míos está cerca de un fuego, con el
 * viento soplando hacia él?». Aquí se responde con campos que **ya se publican**
 * por incendio; no se calcula nada nuevo sobre el fuego, solo la relación con
 * tus puntos.
 *
 * **El fichero no sale del navegador.** No hay subida, no hay servidor, no se
 * guarda nada. La lista de subestaciones de una eléctrica o de fincas de una
 * bodega es información sensible, y la única forma honesta de prometer que no la
 * tocamos es no recibirla nunca. Encaja además con la regla de no meter base de
 * datos en el camino de lectura.
 *
 * **Se dice «expuesto», nunca «en peligro».** Podemos afirmar distancia,
 * sotavento y terreno, que son medidas. Que el fuego vaya a llegar exige un
 * modelo de propagación que no tenemos. Es la misma regla por la que no
 * decimos «activo» sin parte oficial: nada se afirma sin quien lo afirme.
 */

import type { ColeccionIncidentes, PropiedadesIncidente } from '../tipos';

/** El incendio tal como circula por el resto del frontend: geometría + propiedades. */
export type RasgoIncidente = ColeccionIncidentes['features'][number];

export interface Activo {
  nombre: string;
  lat: number;
  lon: number;
}

/** Distancia por debajo de la cual un activo deja de considerarse lejano. */
export const CERCA_KM = 10;

/** Holgura del cono de sotavento, a cada lado de la dirección del viento. */
export const ARCO_SOTAVENTO_GRADOS = 45;

const RADIO_TIERRA_KM = 6371;

const aRadianes = (g: number) => (g * Math.PI) / 180;

/** Distancia en kilómetros sobre la esfera (haversine). */
export function distanciaKm(aLat: number, aLon: number, bLat: number, bLon: number): number {
  const dLat = aRadianes(bLat - aLat);
  const dLon = aRadianes(bLon - aLon);
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(aRadianes(aLat)) * Math.cos(aRadianes(bLat)) * Math.sin(dLon / 2) ** 2;
  return 2 * RADIO_TIERRA_KM * Math.asin(Math.min(1, Math.sqrt(h)));
}

/** Rumbo de A a B en grados desde el norte (0–360). */
export function rumboGrados(aLat: number, aLon: number, bLat: number, bLon: number): number {
  const dLon = aRadianes(bLon - aLon);
  const y = Math.sin(dLon) * Math.cos(aRadianes(bLat));
  const x =
    Math.cos(aRadianes(aLat)) * Math.sin(aRadianes(bLat)) -
    Math.sin(aRadianes(aLat)) * Math.cos(aRadianes(bLat)) * Math.cos(dLon);
  return (((Math.atan2(y, x) * 180) / Math.PI) + 360) % 360;
}

/** Diferencia angular mínima entre dos rumbos (0–180). */
export function diferenciaAngular(a: number, b: number): number {
  const d = Math.abs(a - b) % 360;
  return d > 180 ? 360 - d : d;
}

export interface Exposicion {
  activo: Activo;
  /** Incendio más cercano, o `null` si no hay ninguno publicado. */
  incendio: PropiedadesIncidente | null;
  distanciaKm: number | null;
  /**
   * `true` si el viento sopla del incendio hacia el activo, `false` si no y
   * `null` si el incendio no tiene viento publicado.
   *
   * El nulo importa: «no sabemos hacia dónde sopla» no es «no sopla hacia ti».
   */
  aSotavento: boolean | null;
}

/** Ordena de más expuesto a menos: primero lo cercano, y a igualdad el sotavento. */
function gravedad(e: Exposicion): number {
  if (e.distanciaKm === null) return Number.POSITIVE_INFINITY;
  // El sotavento acerca virtualmente el fuego a efectos de orden, sin tocar la
  // distancia que se muestra, que sigue siendo la real.
  return e.aSotavento === true ? e.distanciaKm / 2 : e.distanciaKm;
}

export function calcularExposicion(
  activos: Activo[],
  incidentes: RasgoIncidente[],
): Exposicion[] {
  const salida = activos.map((activo): Exposicion => {
    let cercano: RasgoIncidente | null = null;
    let minima = Number.POSITIVE_INFINITY;

    for (const rasgo of incidentes) {
      const [lon, lat] = rasgo.geometry?.coordinates ?? [];
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;
      const d = distanciaKm(activo.lat, activo.lon, lat, lon);
      if (d < minima) {
        minima = d;
        cercano = rasgo;
      }
    }

    if (!cercano) {
      return { activo, incendio: null, distanciaKm: null, aSotavento: null };
    }

    // Sotavento: ¿el viento del incendio apunta hacia el activo? Se compara la
    // dirección hacia la que sopla con el rumbo del fuego al punto.
    let aSotavento: boolean | null = null;
    const viento = cercano.properties.viento_hacia_deg;
    if (viento !== null && viento !== undefined) {
      const [lon, lat] = cercano.geometry.coordinates;
      aSotavento =
        diferenciaAngular(viento, rumboGrados(lat, lon, activo.lat, activo.lon)) <=
        ARCO_SOTAVENTO_GRADOS;
    }

    return { activo, incendio: cercano.properties, distanciaKm: minima, aSotavento };
  });

  return salida.sort((a, b) => gravedad(a) - gravedad(b));
}

// --- Lectura de ficheros ----------------------------------------------------

export class ErrorDeFichero extends Error {}

/**
 * CSV con cabecera. Se aceptan varios nombres de columna porque nadie exporta
 * con los mismos: obligar a renombrar columnas antes de poder mirar el mapa es
 * la clase de fricción que hace que la herramienta no se use.
 */
const COL_NOMBRE = ['nombre', 'name', 'activo', 'descripcion', 'descripción', 'id'];
const COL_LAT = ['lat', 'latitud', 'latitude', 'y'];
const COL_LON = ['lon', 'lng', 'long', 'longitud', 'longitude', 'x'];

function indiceDe(cabecera: string[], candidatos: string[]): number {
  return cabecera.findIndex((c) => candidatos.includes(c.trim().toLowerCase()));
}

export function leerCSV(texto: string): Activo[] {
  const lineas = texto.split(/\r?\n/).filter((l) => l.trim());
  if (lineas.length < 2) throw new ErrorDeFichero('El fichero no tiene datos.');

  // Coma o punto y coma: Excel en español exporta con punto y coma.
  const separador = (lineas[0].match(/;/g) || []).length > (lineas[0].match(/,/g) || []).length
    ? ';'
    : ',';

  const cabecera = lineas[0].split(separador);
  const iLat = indiceDe(cabecera, COL_LAT);
  const iLon = indiceDe(cabecera, COL_LON);
  if (iLat < 0 || iLon < 0) {
    throw new ErrorDeFichero('Faltan columnas de latitud y longitud.');
  }
  const iNombre = indiceDe(cabecera, COL_NOMBRE);

  const activos: Activo[] = [];
  for (const [n, linea] of lineas.slice(1).entries()) {
    const campos = linea.split(separador);
    // Coma decimal, otra vez por Excel en español.
    const lat = Number(String(campos[iLat] ?? '').trim().replace(',', '.'));
    const lon = Number(String(campos[iLon] ?? '').trim().replace(',', '.'));
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;
    activos.push({
      nombre: (campos[iNombre] ?? '').trim() || `Punto ${n + 1}`,
      lat,
      lon,
    });
  }

  return validar(activos);
}

export function leerGeoJSON(texto: string): Activo[] {
  let datos: { features?: unknown[] };
  try {
    datos = JSON.parse(texto);
  } catch {
    throw new ErrorDeFichero('El fichero no es un GeoJSON válido.');
  }

  const activos: Activo[] = [];
  for (const [n, rasgo] of (datos.features ?? []).entries()) {
    const f = rasgo as {
      geometry?: { type?: string; coordinates?: number[] };
      properties?: Record<string, unknown>;
    };
    if (f.geometry?.type !== 'Point') continue;
    const [lon, lat] = f.geometry.coordinates ?? [];
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;

    const props = f.properties ?? {};
    const nombre = COL_NOMBRE.map((c) => props[c]).find((v) => typeof v === 'string' && v.trim());
    activos.push({ nombre: (nombre as string) ?? `Punto ${n + 1}`, lat, lon });
  }

  return validar(activos);
}

/** Límite defensivo: más allá el navegador se atasca sin avisar. */
export const MAX_ACTIVOS = 5000;

function validar(activos: Activo[]): Activo[] {
  if (!activos.length) {
    throw new ErrorDeFichero('No se ha encontrado ningún punto con coordenadas.');
  }
  if (activos.length > MAX_ACTIVOS) {
    throw new ErrorDeFichero(`Demasiados puntos (${activos.length}). El máximo es ${MAX_ACTIVOS}.`);
  }

  // Coordenadas invertidas es el error más común y el más difícil de ver: el
  // punto aparece en el mapa, pero en Somalia. En España la longitud es
  // siempre menor que la latitud, así que un lote entero al revés se detecta.
  const invertidos = activos.filter((a) => Math.abs(a.lat) <= 10 && Math.abs(a.lon) >= 30).length;
  if (invertidos > activos.length / 2) {
    throw new ErrorDeFichero(
      'Las coordenadas parecen invertidas: revisa que latitud y longitud no estén cambiadas.',
    );
  }

  return activos;
}

export function leerFichero(nombre: string, texto: string): Activo[] {
  return nombre.toLowerCase().endsWith('.csv') ? leerCSV(texto) : leerGeoJSON(texto);
}

// --- Interfaz ---------------------------------------------------------------

export interface OpcionesActivos {
  /** Se llama al cargar, vaciar o recalcular. `null` cuando ya no hay activos. */
  alCambiar: (activos: Activo[] | null) => void;
  /** Centrar el mapa en un activo al pulsarlo en la lista. */
  alElegir: (activo: Activo) => void;
}

export function construirActivos(nodo: HTMLElement, opciones: OpcionesActivos): void {
  nodo.innerHTML = `
    <p class="activos__intro">
      Sube tus puntos —naves, fincas, campings, torres— y verás cuáles están
      cerca de un incendio y con el viento soplando hacia ellos.
    </p>
    <label class="activos__soltar" for="activos-fichero">
      <input type="file" id="activos-fichero" accept=".csv,.json,.geojson" hidden />
      <span class="activos__soltar-texto">Arrastra un CSV o GeoJSON, o pulsa aquí</span>
      <span class="activos__soltar-formato">CSV con columnas <code>nombre, lat, lon</code></span>
    </label>
    <!-- Esta frase es media venta: una lista de subestaciones o de fincas es
         información sensible, y la única forma honesta de prometer que no la
         tocamos es no recibirla nunca. -->
    <p class="activos__privacidad">
      El fichero <b>no sale de tu navegador</b>. No se sube a ningún servidor ni
      se guarda en ninguna parte.
    </p>
    <p class="activos__error" id="activos-error" role="alert" hidden></p>
    <div class="activos__resultado" id="activos-resultado" hidden></div>
    <button type="button" class="activos__quitar" id="activos-quitar" hidden>
      Quitar mis activos
    </button>`;

  const entrada = nodo.querySelector<HTMLInputElement>('#activos-fichero')!;
  const zona = nodo.querySelector<HTMLLabelElement>('.activos__soltar')!;
  const error = nodo.querySelector<HTMLParagraphElement>('#activos-error')!;
  const quitar = nodo.querySelector<HTMLButtonElement>('#activos-quitar')!;

  const fallar = (mensaje: string) => {
    error.hidden = false;
    error.textContent = mensaje;
    opciones.alCambiar(null);
  };

  const procesar = async (fichero: File) => {
    error.hidden = true;
    try {
      const activos = leerFichero(fichero.name, await fichero.text());
      quitar.hidden = false;
      opciones.alCambiar(activos);
    } catch (e) {
      quitar.hidden = true;
      fallar(e instanceof ErrorDeFichero ? e.message : 'No se ha podido leer el fichero.');
    }
  };

  entrada.addEventListener('change', () => {
    const f = entrada.files?.[0];
    if (f) void procesar(f);
  });

  for (const evento of ['dragover', 'dragenter']) {
    zona.addEventListener(evento, (e) => {
      e.preventDefault();
      zona.classList.add('activos__soltar--activa');
    });
  }
  for (const evento of ['dragleave', 'drop']) {
    zona.addEventListener(evento, () => zona.classList.remove('activos__soltar--activa'));
  }
  zona.addEventListener('drop', (e) => {
    e.preventDefault();
    const f = (e as DragEvent).dataTransfer?.files?.[0];
    if (f) void procesar(f);
  });

  quitar.addEventListener('click', () => {
    entrada.value = '';
    quitar.hidden = true;
    error.hidden = true;
    opciones.alCambiar(null);
  });

  // Delegado: la lista se repinta en cada actualización de datos.
  nodo.addEventListener('click', (e) => {
    const boton = (e.target as HTMLElement).closest<HTMLButtonElement>('[data-activo-lat]');
    if (!boton) return;
    opciones.alElegir({
      nombre: boton.dataset.activoNombre ?? '',
      lat: Number(boton.dataset.activoLat),
      lon: Number(boton.dataset.activoLon),
    });
  });
}

function etiqueta(e: Exposicion): { texto: string; clase: string } {
  if (e.distanciaKm === null) return { texto: 'sin incendios publicados', clase: 'nada' };
  if (e.distanciaKm > CERCA_KM) return { texto: 'sin incendios cerca', clase: 'nada' };
  // El nulo se dice, no se esconde: «no sabemos hacia dónde sopla» no es «no
  // sopla hacia ti».
  if (e.aSotavento === null) return { texto: 'cerca · viento sin dato', clase: 'duda' };
  if (e.aSotavento) return { texto: 'cerca y a sotavento', clase: 'alta' };
  return { texto: 'cerca, viento en contra', clase: 'media' };
}

export function pintarExposicion(exposiciones: Exposicion[]): void {
  const nodo = document.getElementById('activos-resultado');
  if (!nodo) return;

  if (!exposiciones.length) {
    nodo.hidden = true;
    return;
  }

  const cerca = exposiciones.filter((e) => e.distanciaKm !== null && e.distanciaKm <= CERCA_KM);
  nodo.hidden = false;
  nodo.innerHTML = `
    <p class="activos__recuento">
      ${
        cerca.length
          ? `<b>${cerca.length}</b> de ${exposiciones.length} a menos de ${CERCA_KM} km de un incendio.`
          : `Ninguno de tus ${exposiciones.length} puntos tiene un incendio a menos de ${CERCA_KM} km.`
      }
    </p>
    <ul class="activos__lista">
      ${exposiciones
        .slice(0, 50)
        .map((e) => {
          const { texto, clase } = etiqueta(e);
          const dist =
            e.distanciaKm === null
              ? ''
              : `<span class="activos__dist">${e.distanciaKm.toFixed(1)} km</span>`;
          return `
          <li class="activos__fila activos__fila--${clase}">
            <button type="button" class="activos__punto"
                    data-activo-lat="${e.activo.lat}" data-activo-lon="${e.activo.lon}"
                    data-activo-nombre="${e.activo.nombre}">
              <span class="activos__nombre">${e.activo.nombre}</span>
              <span class="activos__estado">${texto}</span>
              ${dist}
            </button>
          </li>`;
        })
        .join('')}
    </ul>
    ${
      exposiciones.length > 50
        ? `<p class="activos__mas">Se muestran los 50 más expuestos de ${exposiciones.length}.</p>`
        : ''
    }
    <p class="activos__aviso">
      «Expuesto» significa <b>cerca y a favor del viento</b>, no que el fuego
      vaya a llegar. Ante una emergencia, <b>112</b>.
    </p>`;
}
