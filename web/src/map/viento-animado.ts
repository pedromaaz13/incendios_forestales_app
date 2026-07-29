/**
 * Animación de viento con partículas, al estilo de Windy.
 *
 * Se implementa como `CustomLayerInterface` de MapLibre sobre un lienzo 2D en
 * lugar de con shaders: son 41 puntos de rejilla y unas centenares de
 * partículas, y el coste de WebGL propio no se justifica frente a lo que
 * complica el código.
 *
 * **De dónde sale la fluidez con solo 41 puntos.** El campo se interpola por
 * distancia inversa ponderada (IDW) sobre los puntos publicados. El resultado
 * es suave pero de poco detalle, que es lo honesto: la resolución real del dato
 * son esos 41 puntos y una animación fina insinuaría una precisión que no hay.
 * Por eso las partículas son tenues y no se dibujan isolíneas ni sombreados que
 * darían aspecto de modelo de alta resolución.
 *
 * **La dirección es hacia donde sopla.** `direction_to_deg` viene ya girada
 * desde el pipeline; aquí no se vuelve a girar. Las partículas se mueven en el
 * sentido en que avanzaría el humo.
 *
 * Respeta `prefers-reduced-motion`: con esa preferencia la capa no se anima y
 * queda el campo de flechas estáticas, que sigue informando.
 */

import type { CustomLayerInterface, Map as MapaGL } from 'maplibre-gl';

export const CAPA_VIENTO_ANIMADO = 'viento-animado';

interface PuntoViento {
  lon: number;
  lat: number;
  /** Componentes en grados por segundo de tiempo de animación. */
  u: number;
  v: number;
  velocidad: number;
}

interface Particula {
  lon: number;
  lat: number;
  edad: number;
  vida: number;
}

/** Cuántos pasos vive una partícula antes de reaparecer en otro sitio. */
const VIDA_MIN = 40;
const VIDA_MAX = 110;

/** Partículas por cada millón de píxeles de lienzo, para que la densidad no
 *  dependa del tamaño de la ventana.
 *
 *  Subida desde 260. Con el valor anterior la capa estaba técnicamente
 *  animando —el lienzo cambiaba entre fotogramas— pero era invisible a simple
 *  vista, y una capa que no se ve es una capa que no existe. Más partículas no
 *  añaden detalle al campo: siguen siendo los mismos 41 puntos interpolados,
 *  solo que ahora se leen. */
const DENSIDAD = 750;

const MAX_PARTICULAS = 2600;

/** Exponente de la ponderación IDW. 2 es el valor habitual: más alto hace el
 *  campo escalonado, más bajo lo aplana hasta perder los contrastes. */
const IDW_POTENCIA = 2;

/** Escala de km/h a grados por paso de animación. Ajustada para que el
 *  movimiento se lea sin que las partículas crucen la pantalla de golpe. */
const ESCALA = 0.00042;

/**
 * Color de la estela por velocidad.
 *
 * Tonos saturados y oscuros, no claros. El primer intento usaba cian y amarillo
 * —bonitos sobre fondo oscuro— y resultaban invisibles: el mapa base por
 * defecto es claro y el amarillo sobre beige no se distingue. Cada estela se
 * dibuja además sobre un trazo blanco más ancho, de modo que se lee igual en
 * el mapa normal, en el de satélite y en el de relieve.
 */
function colorPorVelocidad(kmh: number): string {
  if (kmh < 15) return 'rgba(0, 122, 165, 0.9)';
  if (kmh < 30) return 'rgba(0, 150, 110, 0.92)';
  if (kmh < 50) return 'rgba(214, 105, 0, 0.95)';
  return 'rgba(190, 15, 45, 0.97)';
}

export class CapaVientoAnimado implements CustomLayerInterface {
  readonly id = CAPA_VIENTO_ANIMADO;
  readonly type = 'custom' as const;
  readonly renderingMode = '2d' as const;

  private mapa!: MapaGL;
  private lienzo!: HTMLCanvasElement;
  private ctx!: CanvasRenderingContext2D;
  private particulas: Particula[] = [];
  private animacion = 0;
  private readonly puntos: PuntoViento[];
  private readonly animar: boolean;

  constructor(datos: GeoJSON.FeatureCollection, animar = true) {
    this.animar = animar;
    this.puntos = datos.features.flatMap((f) => {
      const p = f.properties ?? {};
      const c = (f.geometry as GeoJSON.Point)?.coordinates;
      const velocidad = Number(p.speed_kmh);
      const hacia = Number(p.direction_to_deg);
      if (!c || !Number.isFinite(velocidad) || !Number.isFinite(hacia)) return [];

      // `direction_to_deg` es un rumbo: 0° es norte y crece en sentido horario.
      const rad = (hacia * Math.PI) / 180;
      return [
        {
          lon: c[0],
          lat: c[1],
          u: Math.sin(rad) * velocidad * ESCALA,
          v: Math.cos(rad) * velocidad * ESCALA,
          velocidad,
        },
      ];
    });
  }

  onAdd(mapa: MapaGL, _gl: WebGLRenderingContext): void {
    this.mapa = mapa;

    this.lienzo = document.createElement('canvas');
    this.lienzo.className = 'viento-animado';
    const contenedor = mapa.getCanvasContainer();
    contenedor.appendChild(this.lienzo);

    const ctx = this.lienzo.getContext('2d');
    if (!ctx) return;
    this.ctx = ctx;

    this.redimensionar();
    mapa.on('resize', this.redimensionar);
    // Al mover el mapa las partículas quedan en coordenadas geográficas, así
    // que siguen siendo válidas; solo hay que limpiar la estela anterior.
    mapa.on('move', this.limpiar);

    this.sembrar();
    if (this.animar) this.bucle();
    else this.pintarUnaVez();
  }

  onRemove(): void {
    cancelAnimationFrame(this.animacion);
    this.mapa.off('resize', this.redimensionar);
    this.mapa.off('move', this.limpiar);
    this.lienzo.remove();
  }

  /** El pintado va por `requestAnimationFrame`, no por el ciclo del mapa. */
  render(): void {}

  private redimensionar = (): void => {
    const { width, height } = this.mapa.getCanvas();
    const ratio = window.devicePixelRatio || 1;
    this.lienzo.width = width;
    this.lienzo.height = height;
    this.lienzo.style.width = `${width / ratio}px`;
    this.lienzo.style.height = `${height / ratio}px`;
    this.sembrar();
  };

  private limpiar = (): void => {
    this.ctx?.clearRect(0, 0, this.lienzo.width, this.lienzo.height);
  };

  private sembrar(): void {
    const area = (this.lienzo.width * this.lienzo.height) / 1_000_000;
    const cuantas = Math.min(MAX_PARTICULAS, Math.round(area * DENSIDAD));
    this.particulas = Array.from({ length: cuantas }, () => this.nueva());
  }

  private nueva(): Particula {
    const limites = this.mapa.getBounds();
    return {
      lon: limites.getWest() + Math.random() * (limites.getEast() - limites.getWest()),
      lat: limites.getSouth() + Math.random() * (limites.getNorth() - limites.getSouth()),
      edad: 0,
      vida: VIDA_MIN + Math.random() * (VIDA_MAX - VIDA_MIN),
    };
  }

  /**
   * Interpolación por distancia inversa ponderada.
   *
   * Con 41 estaciones repartidas por España, el vecino más próximo daría un
   * campo a parches con saltos bruscos en las fronteras de Voronoi. IDW lo
   * suaviza sin inventar estructura: entre dos puntos el resultado es siempre
   * una mezcla de los dos, nunca un valor que ninguno respalde.
   */
  private muestrear(lon: number, lat: number): PuntoViento | null {
    let su = 0;
    let sv = 0;
    let svel = 0;
    let peso = 0;

    for (const p of this.puntos) {
      const dx = lon - p.lon;
      const dy = lat - p.lat;
      const d2 = dx * dx + dy * dy;

      // Prácticamente encima de una estación: se devuelve su valor tal cual,
      // que además evita la división por cero.
      if (d2 < 1e-8) return p;

      const w = 1 / Math.pow(d2, IDW_POTENCIA / 2);
      su += p.u * w;
      sv += p.v * w;
      svel += p.velocidad * w;
      peso += w;
    }

    if (peso === 0) return null;
    return { lon, lat, u: su / peso, v: sv / peso, velocidad: svel / peso };
  }

  private paso(): void {
    const limites = this.mapa.getBounds();

    // Estela: en vez de borrar, se oscurece lo pintado. Es lo que da la
    // sensación de flujo continuo en lugar de puntos parpadeando.
    // Un borrado más lento deja estelas largas: es lo que convierte puntos que
    // se mueven en líneas de flujo legibles.
    this.ctx.globalCompositeOperation = 'destination-out';
    this.ctx.fillStyle = 'rgba(0, 0, 0, 0.035)';
    this.ctx.fillRect(0, 0, this.lienzo.width, this.lienzo.height);
    this.ctx.globalCompositeOperation = 'source-over';

    const ratio = window.devicePixelRatio || 1;
    this.ctx.lineCap = 'round';

    // Dos pasadas: primero todas las estelas en blanco y algo más anchas, luego
    // el color encima. Ese contorno es lo que las hace legibles sobre el mapa
    // claro, sobre la ortofoto y sobre el relieve sin cambiar de paleta.
    const trazos: Array<{ ax: number; ay: number; bx: number; by: number; color: string }> = [];

    for (const p of this.particulas) {
      const v = this.muestrear(p.lon, p.lat);
      if (!v) continue;

      const lon2 = p.lon + v.u;
      const lat2 = p.lat + v.v;

      const a = this.mapa.project([p.lon, p.lat]);
      const b = this.mapa.project([lon2, lat2]);

      trazos.push({
        ax: a.x * ratio, ay: a.y * ratio,
        bx: b.x * ratio, by: b.y * ratio,
        color: colorPorVelocidad(v.velocidad),
      });

      p.lon = lon2;
      p.lat = lat2;
      p.edad += 1;

      const fuera =
        lon2 < limites.getWest() ||
        lon2 > limites.getEast() ||
        lat2 < limites.getSouth() ||
        lat2 > limites.getNorth();

      // Reaparecer al envejecer, y no solo al salir del encuadre, evita que
      // todas las partículas acaben apelotonadas donde el campo converge.
      if (p.edad > p.vida || fuera) Object.assign(p, this.nueva());
    }

    // Contorno claro debajo de todas las estelas.
    this.ctx.strokeStyle = 'rgba(255, 255, 255, 0.55)';
    this.ctx.lineWidth = 3.6 * ratio;
    this.ctx.beginPath();
    for (const t of trazos) {
      this.ctx.moveTo(t.ax, t.ay);
      this.ctx.lineTo(t.bx, t.by);
    }
    this.ctx.stroke();

    // Y el color encima. Se agrupa por color para no cambiar de estilo en cada
    // trazo: con 2.600 partículas eso es la diferencia entre 60 fps y arrastrar.
    this.ctx.lineWidth = 1.9 * ratio;
    const porColor = new Map<string, typeof trazos>();
    for (const t of trazos) {
      const lote = porColor.get(t.color);
      if (lote) lote.push(t);
      else porColor.set(t.color, [t]);
    }
    for (const [color, lote] of porColor) {
      this.ctx.strokeStyle = color;
      this.ctx.beginPath();
      for (const t of lote) {
        this.ctx.moveTo(t.ax, t.ay);
        this.ctx.lineTo(t.bx, t.by);
      }
      this.ctx.stroke();
    }
  }

  private bucle = (): void => {
    this.paso();
    this.animacion = requestAnimationFrame(this.bucle);
  };

  /** Sin animación se pintan unos pasos y se deja el rastro quieto. */
  private pintarUnaVez(): void {
    for (let i = 0; i < 60; i++) this.paso();
  }
}
