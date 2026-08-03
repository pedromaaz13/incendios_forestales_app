/**
 * Buscar tu sitio · 37.497 núcleos de población, en local.
 *
 * Por qué no un geocoder externo: la especificación lo prohíbe (RF-P-07) y las
 * razones valen igual en el navegador — límite de peticiones, dependencia de un
 * tercero en el camino crítico y latencia. El índice ya lo tenemos del IGN.
 *
 * Por qué se carga tarde. El índice son ~520 KB comprimidos y el presupuesto de
 * carga inicial de todo el visor son 900 KB. Quien nunca busca no debería
 * pagarlo, así que se pide al primer tecleo y se guarda para el resto de la
 * sesión.
 *
 * La búsqueda ignora acentos a propósito: quien está asustado escribe «avila»
 * desde el móvil, no «Ávila».
 */

/** [nombre, lat, lon, habitantes] — la forma que emite preparar_indice_nucleos.py */
type Fila = [string, number, number, number];

export interface Nucleo {
  nombre: string;
  lat: number;
  lon: number;
  habitantes: number;
}

export const MAX_RESULTADOS = 8;

/** Quita acentos y pasa a minúsculas para comparar. */
export function normalizar(texto: string): string {
  return texto
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .trim();
}

/**
 * Ordena por dónde aparece lo escrito y, a igualdad, por población.
 *
 * Que empiece por lo tecleado pesa más que contenerlo: buscando «Ávila» se
 * quiere Ávila, no «Peñalba de Ávila». Y ante decenas de «Villanueva de…», el
 * que casi siempre se busca es el más poblado.
 */
export function buscar(indice: Nucleo[], consulta: string, max = MAX_RESULTADOS): Nucleo[] {
  const q = normalizar(consulta);
  if (q.length < 2) return [];

  const empiezan: Nucleo[] = [];
  const contienen: Nucleo[] = [];

  for (const n of indice) {
    const nombre = normalizar(n.nombre);
    if (nombre.startsWith(q)) empiezan.push(n);
    else if (nombre.includes(q)) contienen.push(n);
    // El índice ya viene ordenado por población, así que cortar en cuanto haya
    // suficientes prefijos evita recorrer 37.497 entradas en cada pulsación.
    if (empiezan.length >= max) break;
  }

  return [...empiezan, ...contienen].slice(0, max);
}

let indice: Nucleo[] | null = null;
let cargando: Promise<Nucleo[]> | null = null;

export async function cargarIndice(url = 'nucleos-indice.json'): Promise<Nucleo[]> {
  if (indice) return indice;
  // Sin esta guarda, teclear rápido dispara una descarga por letra.
  if (cargando) return cargando;

  cargando = fetch(url)
    .then((r) => {
      if (!r.ok) throw new Error(`índice de núcleos: HTTP ${r.status}`);
      return r.json();
    })
    .then((filas: Fila[]) => {
      indice = filas.map(([nombre, lat, lon, habitantes]) => ({
        nombre,
        lat,
        lon,
        habitantes,
      }));
      return indice;
    })
    .finally(() => {
      cargando = null;
    });

  return cargando;
}

/** Solo para pruebas: deja el módulo como recién cargado. */
export function _reiniciar(): void {
  indice = null;
  cargando = null;
}

export interface OpcionesBuscador {
  alElegir: (nucleo: Nucleo) => void;
}

export function construirBuscador(nodo: HTMLElement, opciones: OpcionesBuscador): void {
  nodo.innerHTML = `
    <label class="buscador__etiqueta" for="buscador-campo">Busca tu sitio</label>
    <input id="buscador-campo" class="buscador__campo" type="search"
           autocomplete="off" placeholder="Tu pueblo, tu finca, tu camping…"
           aria-describedby="buscador-ayuda" role="combobox" aria-expanded="false"
           aria-controls="buscador-lista" />
    <p id="buscador-ayuda" class="buscador__ayuda">
      Búsqueda local sobre los núcleos de población del IGN.
    </p>
    <ul id="buscador-lista" class="buscador__lista" role="listbox" hidden></ul>`;

  const campo = nodo.querySelector<HTMLInputElement>('#buscador-campo')!;
  const lista = nodo.querySelector<HTMLUListElement>('#buscador-lista')!;

  const pintar = (resultados: Nucleo[]) => {
    campo.setAttribute('aria-expanded', String(resultados.length > 0));
    lista.hidden = resultados.length === 0;
    lista.innerHTML = resultados
      .map(
        (n, i) => `
        <li role="option" id="buscador-op-${i}">
          <button type="button" class="buscador__opcion" data-i="${i}">
            <span class="buscador__nombre">${n.nombre}</span>
            ${
              n.habitantes > 0
                ? `<span class="buscador__hab">${n.habitantes.toLocaleString('es-ES')} hab.</span>`
                : ''
            }
          </button>
        </li>`,
      )
      .join('');

    for (const boton of lista.querySelectorAll<HTMLButtonElement>('[data-i]')) {
      boton.addEventListener('click', () => {
        const elegido = resultados[Number(boton.dataset.i)];
        campo.value = elegido.nombre;
        pintar([]);
        opciones.alElegir(elegido);
      });
    }
  };

  campo.addEventListener('input', async () => {
    const consulta = campo.value;
    if (normalizar(consulta).length < 2) {
      pintar([]);
      return;
    }
    try {
      const datos = await cargarIndice();
      // Entre la descarga y ahora el usuario ha podido seguir escribiendo; si
      // el campo ya no dice lo mismo, este resultado está obsoleto.
      if (campo.value !== consulta) return;
      pintar(buscar(datos, consulta));
    } catch {
      lista.hidden = false;
      lista.innerHTML = `<li class="buscador__error">No se ha podido cargar el buscador.</li>`;
    }
  });

  // Escape cierra la lista sin borrar lo escrito.
  campo.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') pintar([]);
  });
}
