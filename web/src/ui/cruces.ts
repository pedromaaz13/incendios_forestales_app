/**
 * Cruces entre capas · preguntar a los datos.
 *
 * Todo lo que hay aquí se responde con campos **que ya se publican** por
 * incendio: el terreno donde cae, la distancia al pueblo más cercano, el viento
 * observado en ese punto, el aviso de AEMET vigente sobre la comarca y quién
 * confirma su estado. Ninguno se calcula aquí; lo único que falta era **poder
 * preguntarlo**.
 *
 * Por qué preguntas hechas y no un constructor de consultas. Un formulario con
 * campo, operador y valor es más potente y no lo usaría nadie: obliga a saber
 * qué campos hay antes de poder mirar. Estas cinco son las que alguien se hace
 * de verdad delante de un mapa de incendios, y cada una deja ver qué combinación
 * la produce, así que también enseñan qué se puede cruzar.
 *
 * **Esto no es una herramienta de decisión operativa.** Un panel que cruza
 * variables invita a leerse como tal, y no lo somos: no tenemos autoridad de
 * emergencias ni datos en tiempo real. Si alguien decidiera evacuar mirando esto
 * en vez de llamando al 112, habríamos hecho daño. Por eso cada cruce publica
 * también **cuántos incendios se quedan fuera por falta de dato**, y el aviso de
 * que no sustituye al 112 no se oculta nunca.
 */

import type { PropiedadesIncidente } from '../tipos';

export interface Cruce {
  id: string;
  /** Cómo se lee la pregunta. En segunda persona, como se la haría alguien. */
  pregunta: string;
  /** Qué combinación de campos la responde, para que se vea qué se está cruzando. */
  criterio: string;
  cumple: (p: PropiedadesIncidente) => boolean;
  /**
   * Campos sin los que la pregunta no se puede responder para ese incendio.
   * Sin esto, un incendio al que le falta el viento contaría como «no cumple»,
   * que es afirmar algo que no sabemos.
   */
  requiere: (keyof PropiedadesIncidente)[];
}

/** Viento que empieza a importar de verdad para la propagación. */
const VIENTO_FUERTE_KMH = 20;

/** Distancia por debajo de la cual un incendio deja de ser «lejos». */
const CERCA_KM = 2;

export const CRUCES: Cruce[] = [
  {
    id: 'monte-cerca-viento',
    pregunta: 'En monte, cerca de un pueblo y con viento',
    criterio: `forestal · núcleo a menos de ${CERCA_KM} km · viento ≥ ${VIENTO_FUERTE_KMH} km/h`,
    requiere: ['suelo_tipo', 'nucleo_cercano_km', 'viento_kmh'],
    cumple: (p) =>
      p.suelo_tipo === 'forestal' &&
      (p.nucleo_cercano_km ?? Infinity) <= CERCA_KM &&
      (p.viento_kmh ?? 0) >= VIENTO_FUERTE_KMH,
  },
  {
    id: 'monte-cerca',
    pregunta: 'En monte y cerca de un pueblo',
    criterio: `forestal · núcleo a menos de ${CERCA_KM} km`,
    requiere: ['suelo_tipo', 'nucleo_cercano_km'],
    cumple: (p) =>
      p.suelo_tipo === 'forestal' && (p.nucleo_cercano_km ?? Infinity) <= CERCA_KM,
  },
  {
    id: 'bajo-aviso',
    pregunta: 'Con aviso de AEMET vigente en su zona',
    criterio: 'aviso amarillo o superior sobre la comarca',
    requiere: ['aviso_nivel'],
    cumple: (p) => Boolean(p.aviso_nivel),
  },
  {
    id: 'confirmado',
    pregunta: 'Confirmados por un servicio de extinción',
    criterio: 'con parte oficial · estado declarado',
    requiere: [],
    cumple: (p) => Boolean(p.official_confirmed),
  },
  {
    id: 'probable-agricola',
    pregunta: 'Probablemente quema agrícola, no incendio forestal',
    criterio: 'sobre cultivo · sin parte oficial',
    requiere: ['suelo_tipo'],
    cumple: (p) => p.suelo_tipo === 'agrícola' && !p.official_confirmed,
  },
];

export interface Resultado {
  cumplen: string[];
  /** Incendios a los que les falta algún dato para poder responder. */
  sinDato: number;
}

/**
 * Aplica un cruce y separa los que no se pueden evaluar.
 *
 * La distinción importa: «no cumple» y «no se sabe» son cosas distintas, y
 * juntarlas convertiría un hueco de datos en una afirmación. Un incendio sin
 * viento interpolado no es un incendio sin viento.
 */
export function aplicarCruce(
  incidentes: PropiedadesIncidente[],
  cruce: Cruce,
): Resultado {
  const cumplen: string[] = [];
  let sinDato = 0;

  for (const p of incidentes) {
    const falta = cruce.requiere.some((campo) => p[campo] === null || p[campo] === undefined);
    if (falta) {
      sinDato += 1;
      continue;
    }
    if (cruce.cumple(p)) cumplen.push(p.id);
  }

  return { cumplen, sinDato };
}

export interface OpcionesCruces {
  /** `null` cuando se apaga el cruce y se vuelve a ver todo. */
  alElegir: (cruce: Cruce | null) => void;
}

export function construirCruces(nodo: HTMLElement, opciones: OpcionesCruces): void {
  nodo.innerHTML = `
    <p class="cruces__intro">
      Cruza lo que ya se sabe de cada incendio: el terreno, la distancia al
      pueblo más cercano, el viento observado y los avisos vigentes.
    </p>
    <div class="cruces__lista" role="group" aria-label="Cruces entre capas">
      ${CRUCES.map(
        (c) => `
        <button type="button" class="cruce" data-cruce="${c.id}" aria-pressed="false">
          <span class="cruce__pregunta">${c.pregunta}</span>
          <span class="cruce__criterio">${c.criterio}</span>
        </button>`,
      ).join('')}
    </div>
    <p class="cruces__resultado" id="cruce-resultado" hidden></p>
    <!-- El aviso va aquí y no solo al pie: un panel que cruza variables invita
         a leerse como herramienta de decisión, y este es el sitio donde esa
         lectura equivocada empieza. -->
    <p class="cruces__aviso">
      Sirve para <b>entender</b> lo que está pasando, no para decidir.
      Ante una emergencia, <b>112</b>.
    </p>`;

  let activo: string | null = null;

  for (const boton of nodo.querySelectorAll<HTMLButtonElement>('[data-cruce]')) {
    boton.addEventListener('click', () => {
      const id = boton.dataset.cruce!;
      // Volver a pulsar el mismo lo apaga: sin esto no habría forma de deshacer
      // el cruce sin recargar, y quedarse atrapado en un filtro es la manera más
      // fácil de creer que hay menos incendios de los que hay.
      activo = activo === id ? null : id;

      for (const otro of nodo.querySelectorAll<HTMLButtonElement>('[data-cruce]')) {
        otro.setAttribute('aria-pressed', String(otro.dataset.cruce === activo));
      }

      opciones.alElegir(activo ? (CRUCES.find((c) => c.id === activo) ?? null) : null);
    });
  }
}

/** Pinta el recuento del cruce activo. */
export function pintarResultado(
  total: number,
  resultado: Resultado | null,
): void {
  const nodo = document.getElementById('cruce-resultado');
  if (!nodo) return;

  if (!resultado) {
    nodo.hidden = true;
    return;
  }

  nodo.hidden = false;
  const n = resultado.cumplen.length;

  // El texto dice el total además del filtrado: «3» a secas no informa de nada
  // si no se sabe de cuántos.
  const base = n === 0
    ? `Ninguno de los ${total} incendios visibles cumple.`
    : `<b>${n}</b> de ${total} incendios visibles.`;

  // Y los que no se han podido evaluar, que es lo que separa un hueco de datos
  // de una afirmación.
  const huecos = resultado.sinDato
    ? ` <span class="cruces__huecos">${resultado.sinDato} sin datos suficientes para responder.</span>`
    : '';

  nodo.innerHTML = base + huecos;
}
