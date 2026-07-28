/**
 * Contrato de datos publicado por el pipeline · secciones 4.1–4.3.
 *
 * Estos tipos son el espejo exacto de lo que escribe `src/incendios/build.py`.
 * Si cambian ahí sin cambiar aquí, `tsc` no se entera —los datos llegan como
 * JSON en tiempo de ejecución—, así que hay una comprobación de esquema en
 * `datos.ts` que sí se entera.
 */

export type Origen = 'satelite' | 'oficial' | 'ambos';
export type Estado = 'activo' | 'estabilizado' | 'controlado' | 'extinguido';
export type Intensidad = 'baja' | 'media' | 'alta' | 'extrema';
export type EstadoFuente = 'ok' | 'stale' | 'error' | 'disabled';

export interface PropiedadesIncidente {
  id: string;
  origin: Origen;
  satellite_confirmed: boolean;
  official_confirmed: boolean;
  confirmed_by: string;
  status: Estado;
  municipio: string | null;
  provincia: string | null;
  igr_level: number | null;
  resources_air: number | null;
  resources_ground: number | null;
  resources_people: number | null;
  resources_text?: string | null;
  n_hotspots: number;
  frp_total_mw: number | null;
  intensity: Intensidad | null;
  area_est_ha: number | null;
  /** Gobierna el radio del anillo de incertidumbre. Siempre > 0 (invariante 6). */
  position_precision_m: number;
  first_detected: string | null;
  last_detected: string | null;
  started_at: string | null;
  /** Añadido en cliente: radio en píxeles a zoom 0, para el anillo métrico. */
  _radio_base?: number;
  /** Añadido en cliente: la lista y la ficha se pintan desde las propiedades,
   *  sin acceso a la geometría, y necesitan las coordenadas para poder decir
   *  dónde está un incendio que todavía no tiene municipio. */
  _lon?: number;
  _lat?: number;
}

export interface PropiedadesHotspot {
  acq_dt: string;
  frp_mw: number;
  confidence_pct: number;
  fire_id: string | null;
  instrument: string;
  daynight: string;
}

export interface PropiedadesViento {
  name: string;
  speed_kmh: number;
  gusts_kmh: number | null;
  direction_from_deg: number;
  /** El que dibuja la flecha: hacia dónde sopla, no de dónde viene. */
  direction_to_deg: number;
  cardinal_from: string;
  observed_at: string | null;
}

export interface Manifiesto {
  schema_version: number;
  generated_at: string;
  pipeline_age_seconds: number;
  data_age_seconds: Record<string, number>;
  worst_data_age_seconds: number | null;
  counts: {
    incidents_total: number;
    incidents_satellite_confirmed: number;
    incidents_official_only: number;
    hotspots_24h: number;
    hotspots_suppressed_industrial: number;
    hotspots_suppressed_lowconf: number;
  };
  frp_total_mw: number;
  degraded: boolean;
  degraded_reason: string | null;
  disclaimer: string;
  demo?: boolean;
  demo_reason?: string;
}

export interface Fuente {
  id: string;
  name: string;
  region: string;
  kind: 'oficial' | 'satelite' | 'contexto';
  critical: boolean;
  status: EstadoFuente;
  last_success_at: string | null;
  age_seconds: number | null;
  ttl_seconds: number;
  records: number;
  precision_m: number | null;
  error: string | null;
  consecutive_failures: number;
  attribution: string;
}

export interface Salud {
  generated_at: string;
  sources: Fuente[];
}

export type ColeccionIncidentes = GeoJSON.FeatureCollection<
  GeoJSON.Point,
  PropiedadesIncidente
>;

export interface PropiedadesAire {
  name: string;
  /** Índice europeo de calidad del aire. No es un porcentaje: 0 a >100. */
  aqi: number;
  nivel: string;
  pm2_5: number | null;
  pm10: number | null;
  co: number | null;
  observed_at: string | null;
}
