/**
 * Formateo de cifras y tiempos para la interfaz.
 *
 * Regla de dominio que atraviesa todo el fichero: **nunca verbos de certeza**.
 * Se dice "detectado" y "estimación", no "hay" ni "arde". Y ninguna cifra se
 * redondea hacia arriba: una superficie estimada de 168 ha no se presenta como
 * "unas 200 ha", porque el redondeo de conveniencia es el primer paso hacia el
 * dato inventado.
 */

const UMBRAL_VERDE_S = 3600; // 1 h
const UMBRAL_AMBAR_S = 14400; // 4 h

export type NivelLatencia = 'ok' | 'warn' | 'bad' | 'nulo';

/** Umbrales de color de RF-F-05: verde < 1 h · ámbar 1–4 h · rojo > 4 h. */
export function nivelLatencia(segundos: number | null | undefined): NivelLatencia {
  if (segundos === null || segundos === undefined) return 'nulo';
  if (segundos < UMBRAL_VERDE_S) return 'ok';
  if (segundos <= UMBRAL_AMBAR_S) return 'warn';
  return 'bad';
}

/**
 * Duración legible. Sin datos devuelve un guion, nunca "0 min": un cero se lee
 * como "recién actualizado" justo cuando no hay nada que mostrar.
 */
export function duracion(segundos: number | null | undefined): string {
  if (segundos === null || segundos === undefined || !Number.isFinite(segundos)) {
    return '—';
  }
  const s = Math.max(0, Math.round(segundos));
  if (s < 60) return `${s} s`;

  const minutos = Math.floor(s / 60);
  if (minutos < 60) return `${minutos} min`;

  const horas = Math.floor(minutos / 60);
  const resto = minutos % 60;
  if (horas < 24) return resto ? `${horas} h ${resto} min` : `${horas} h`;

  const dias = Math.floor(horas / 24);
  return `${dias} d ${horas % 24} h`;
}

export function haceCuanto(segundos: number | null | undefined): string {
  const d = duracion(segundos);
  return d === '—' ? 'sin datos' : `hace ${d}`;
}

/** Antigüedad en segundos de una marca ISO, o null si no es interpretable. */
export function edadDesde(iso: string | null | undefined, ahora = Date.now()): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  return Math.max(0, Math.round((ahora - t) / 1000));
}

export function fechaHora(iso: string | null | undefined): string {
  if (!iso) return '—';
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return '—';
  return t.toLocaleString('es-ES', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function numero(valor: number | null | undefined, decimales = 0): string {
  if (valor === null || valor === undefined || !Number.isFinite(valor)) return '—';
  return valor.toLocaleString('es-ES', {
    minimumFractionDigits: decimales,
    maximumFractionDigits: decimales,
  });
}

/**
 * Margen de posición en lenguaje llano. Es el texto que acompaña al anillo de
 * incertidumbre y la razón de ser de RF-F-03: un incidente de INFOCAM con
 * ±6 km tiene que decirlo, no fingir un punto.
 */
export function margenPosicion(metros: number | null | undefined): string {
  if (!metros || !Number.isFinite(metros) || metros <= 0) return 'margen no declarado';
  if (metros < 1000) return `±${Math.round(metros)} m`;
  const km = metros / 1000;
  return `±${km.toFixed(km < 10 ? 1 : 0)} km`;
}

const NOMBRES_FUENTE: Record<string, string> = {
  jcyl: 'Castilla y León',
  bombers: 'Cataluña',
  infoca: 'Andalucía',
  infocam: 'Castilla-La Mancha',
  '112cv': 'Comunitat Valenciana',
  firms_viirs: 'NASA FIRMS VIIRS',
  firms_modis: 'NASA FIRMS MODIS',
  seviri: 'EUMETSAT SEVIRI',
};

const SIGLAS_FUENTE: Record<string, string> = {
  jcyl: 'CyL',
  bombers: 'CAT',
  infoca: 'AND',
  infocam: 'CLM',
  '112cv': 'CV',
};

export function nombreFuente(id: string): string {
  return NOMBRES_FUENTE[id] ?? id;
}

export function siglasFuente(confirmadoPor: string): string | null {
  const primera = confirmadoPor.split(',').map((s) => s.trim()).filter(Boolean)[0];
  if (!primera) return null;
  return SIGLAS_FUENTE[primera] ?? primera.toUpperCase().slice(0, 4);
}

/** "confirmado por Castilla y León y Andalucía" */
export function listaFuentes(confirmadoPor: string): string {
  const partes = confirmadoPor
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)
    .map(nombreFuente);
  if (partes.length === 0) return 'sin confirmación oficial';
  if (partes.length === 1) return partes[0];
  return `${partes.slice(0, -1).join(', ')} y ${partes[partes.length - 1]}`;
}

const ETIQUETA_ESTADO: Record<string, string> = {
  activo: 'Activo',
  estabilizado: 'Estabilizado',
  controlado: 'Controlado',
  extinguido: 'Extinguido',
};

export function etiquetaEstado(estado: string): string {
  return ETIQUETA_ESTADO[estado] ?? 'Estado no facilitado';
}

const ETIQUETA_FUENTE_ESTADO: Record<string, string> = {
  ok: 'correcto',
  stale: 'sin datos recientes',
  error: 'sin respuesta',
  disabled: 'sin configurar',
};

export function etiquetaEstadoFuente(estado: string): string {
  return ETIQUETA_FUENTE_ESTADO[estado] ?? estado;
}

/** Icono textual del estado. RF-F-06 prohíbe transmitirlo solo por color. */
export function iconoEstadoFuente(estado: string): string {
  switch (estado) {
    case 'ok':
      return '●';
    case 'stale':
      return '◐';
    case 'error':
      return '✕';
    default:
      return '○';
  }
}


/**
 * Coordenadas legibles, como último recurso cuando no hay municipio.
 *
 * Sin la capa del IGN todos los incidentes salen como "Ubicación por
 * determinar", que es honesto y completamente inútil: alguien que mira el
 * listado no puede saber si le pilla cerca. Cuatro decimales son ~11 m, de
 * sobra para localizarlo en el mapa y sin fingir una precisión que el propio
 * `position_precision_m` ya desmiente.
 */
export function coordenadas(lon: number, lat: number): string {
  const ns = lat >= 0 ? 'N' : 'S';
  const eo = lon >= 0 ? 'E' : 'O';
  return `${Math.abs(lat).toFixed(4)}° ${ns}, ${Math.abs(lon).toFixed(4)}° ${eo}`;
}


const SENSOR_LEGIBLE: Record<string, string> = {
  VIIRS_SNPP_NRT: 'VIIRS',
  VIIRS_NOAA20_NRT: 'VIIRS',
  VIIRS_NOAA21_NRT: 'VIIRS',
  MODIS_NRT: 'MODIS',
  SEVIRI_FRP_PIXEL: 'SEVIRI',
};

/**
 * Sensores que vieron el incendio, sin repetir.
 *
 * Los tres VIIRS (S-NPP, NOAA-20, NOAA-21) son el mismo instrumento en
 * satélites distintos: enumerarlos por separado ocupa la ficha sin decir nada
 * nuevo. Lo que sí cambia la lectura es VIIRS frente a MODIS, porque son
 * 375 m frente a 1 km de resolución.
 */
export function sensores(lista: string | null | undefined): string {
  if (!lista) return '—';
  const vistos = [
    ...new Set(
      lista
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean)
        .map((s) => SENSOR_LEGIBLE[s] ?? s),
    ),
  ];
  return vistos.length ? vistos.join(' · ') : '—';
}

/** Resolución del mejor sensor que vio el incendio, para matizar la posición. */
export function resolucionSensor(lista: string | null | undefined): string | null {
  const v = sensores(lista);
  if (v.includes('VIIRS')) return '375 m';
  if (v.includes('SEVIRI')) return '3 km';
  if (v.includes('MODIS')) return '1 km';
  return null;
}

/**
 * Frase de viento para la ficha.
 *
 * Se escribe el punto cardinal de origen y el de destino a la vez —«del NO,
 * sopla hacia el SE»— porque en castellano el viento se nombra por su origen
 * pero lo que le interesa a quien mira el mapa es a dónde va. Dar solo uno de
 * los dos obliga a hacer la resta mentalmente, y es donde la gente se equivoca.
 */
export function fraseViento(p: {
  viento_kmh: number | null;
  viento_rachas_kmh: number | null;
  viento_hacia_deg: number | null;
  viento_cardinal_desde: string | null;
}): string | null {
  if (p.viento_kmh === null || p.viento_hacia_deg === null) return null;

  const desde = p.viento_cardinal_desde ?? '—';
  const hacia = cardinal(p.viento_hacia_deg);
  const rachas =
    p.viento_rachas_kmh !== null && p.viento_rachas_kmh > p.viento_kmh + 5
      ? `, rachas de ${Math.round(p.viento_rachas_kmh)} km/h`
      : '';

  return `Del ${desde} a ${Math.round(p.viento_kmh)} km/h${rachas}. Sopla hacia el ${hacia}.`;
}

const CARDINALES = [
  'N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE',
  'S', 'SSO', 'SO', 'OSO', 'O', 'ONO', 'NO', 'NNO',
] as const;

/** Punto cardinal en castellano: O de oeste, no W. */
export function cardinal(grados: number): string {
  return CARDINALES[Math.round((grados % 360) / 22.5) % 16];
}
