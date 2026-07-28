/**
 * Punto de entrada del visor.
 *
 * Orquesta cuatro cosas: el mapa, la carga de datos, el estado en la URL y los
 * paneles. El orden de arranque no es casual — primero se comprueba WebGL
 * (RNF-08), luego se pinta la latencia aunque no haya datos (RF-F-13), y solo
 * después se intentan cargar las capas. Así un fallo de red deja un mapa
 * navegable con una banda que lo explica, nunca una pantalla en blanco.
 */

import maplibregl, { type Map as MapaGL } from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import './estilos.css';

import { cargarGeoJson, cargarIncidentes, cargarManifiesto, cargarSalud } from './datos';
import {
  CAPA_AIRE,
  CAPA_TRAFICO,
  CAPA_TRAFICO_INCENDIO,
  CAPA_HOTSPOTS,
  CAPA_INCIDENTES,
  CAPA_PERIMETRO_EFFIS,
  CAPA_PERIMETRO_ESTIMADO,
  CAPA_RESALTE,
  CAPA_VIENTO,
  FUENTE_HOTSPOTS,
  FUENTE_INCIDENTES,
  FUENTE_PERIMETROS,
  FUENTE_AIRE,
  FUENTE_TRAFICO,
  FUENTE_VIENTO,
  anadirCapaAire,
  anadirCapasTrafico,
  anadirCapaHotspots,
  anadirCapaViento,
  anadirCapasIncidentes,
  anadirCapasPerimetros,
} from './map/capas';
import { ESTILOS, ESTILO_POR_DEFECTO, esClaveEstilo, type ClaveEstilo } from './map/estilos';
import { abrirFicha, cerrarFicha } from './ui/ficha';
import {
  FILTROS_INICIALES,
  aplicar as aplicarFiltros,
  construirControles as construirFiltros,
  pasaElFiltro,
  type EstadoFiltros,
} from './ui/filtros';
import { pintarLista } from './ui/lista';
import {
  pintarAtribucion,
  pintarBanda,
  pintarFuentes,
  pintarLatencia,
  pintarResumen,
} from './ui/paneles';
import type { ColeccionIncidentes, Manifiesto, PropiedadesIncidente, Salud } from './tipos';

/** Vista inicial: España completa, incluidas Canarias en el encuadre por defecto. */
const VISTA_ESPANA = { center: [-3.7, 40.0] as [number, number], zoom: 5.1 };

/** El cron corre cada 10 min; se refresca algo antes para no ir siempre tarde. */
const REFRESCO_MS = 5 * 60 * 1000;

const CLAVE_ESTILO = 'incendios:estilo';

interface Estado {
  mapa: MapaGL | null;
  manifiesto: Manifiesto | null;
  salud: Salud | null;
  incidentes: ColeccionIncidentes | null;
  seleccionado: string | null;
  capas: Record<string, boolean>;
  filtros: EstadoFiltros;
}

const estado: Estado = {
  mapa: null,
  manifiesto: null,
  salud: null,
  incidentes: null,
  seleccionado: null,
  capas: { hotspots: true, perimetros: false, viento: false, aire: false, trafico: false },
  filtros: { ...FILTROS_INICIALES, sensores: new Set(FILTROS_INICIALES.sensores) },
};

/**
 * El `id` del enlace profundo se lee **antes** de crear el mapa.
 *
 * `moveend` dispara durante el asentamiento inicial de la vista y llama a
 * `sincronizarUrl`, que reescribe la query con el incidente seleccionado —
 * todavía ninguno—. Eso borraba el `?id=` de la URL antes de que
 * `abrirDesdeUrl` llegara a leerlo, y el enlace permanente no abría nada.
 */
const ID_INICIAL = new URLSearchParams(location.search).get('id');

// --- arranque ---------------------------------------------------------------

function hayWebGL(): boolean {
  // MapLibre 4 retiró `maplibregl.supported()`, así que la comprobación se hace
  // a mano. Se prueban los dos contextos: hay navegadores con WebGL 2 detrás de
  // una bandera y WebGL 1 disponible, y con uno basta para pintar el mapa.
  try {
    const lienzo = document.createElement('canvas');
    return Boolean(
      lienzo.getContext('webgl2') ??
        lienzo.getContext('webgl') ??
        lienzo.getContext('experimental-webgl'),
    );
  } catch {
    return false;
  }
}

async function arrancar(): Promise<void> {
  pintarLatencia(null);

  if (!hayWebGL()) {
    // RNF-08: mensaje explícito, nunca pantalla en blanco.
    document.getElementById('sin-webgl')!.hidden = false;
    pintarBanda(
      'Este navegador no puede mostrar el mapa. Los incendios siguen listados ' +
        'en el panel lateral.',
      'error',
    );
    await cargarDatos();
    // Sin mapa no hay viewport contra el que recortar, así que se listan todos.
    // Quedarse solo con el mensaje de error sería perder justo lo que la
    // persona ha venido a consultar, que es qué está ardiendo y dónde.
    pintarLista(
      (estado.incidentes?.features ?? []).map((f) => f.properties),
      manejadoresSinMapa(),
    );
    return;
  }

  const estilo = estiloGuardado();
  const inicial = vistaDesdeUrl();

  const mapa = new maplibregl.Map({
    container: 'mapa',
    style: ESTILOS[estilo].estilo,
    center: inicial.center,
    zoom: inicial.zoom,
    maxZoom: 16,
    attributionControl: { compact: true },
    // RNF-09: sin animaciones si el sistema lo pide.
    fadeDuration: prefiereMenosMovimiento() ? 0 : 300,
  });
  estado.mapa = mapa;

  mapa.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
  mapa.addControl(new maplibregl.FullscreenControl(), 'top-right');
  mapa.addControl(
    new maplibregl.GeolocateControl({ trackUserLocation: false }),
    'top-right',
  );
  mapa.addControl(new maplibregl.ScaleControl({ maxWidth: 120, unit: 'metric' }), 'bottom-right');
  anadirIndicadorZoom(mapa);

  // Los datos NO cuelgan de `load` del mapa. Si el servidor de teselas está
  // caído o bloqueado —firewall corporativo, bloqueador de anuncios—, el evento
  // `load` puede no llegar nunca, y colgar de él la carga dejaría los paneles
  // vacíos y sin explicación. Los datos se piden ya; las capas esperan al mapa.
  const datosListos = cargarDatos().then(() => {
    refrescarLista();
  });

  mapa.on('load', () => {
    void datosListos.then(() => {
      montarCapas(mapa);
      conectarInteraccion(mapa);
      aplicarFiltros(mapa, estado.filtros);
      refrescarLista();
      abrirDesdeUrl();
    });
  });

  mapa.on('moveend', () => {
    sincronizarUrl(mapa);
    refrescarLista();
  });

  construirSelectorEstilo(mapa);
  construirConmutadores(mapa);
  construirFiltros(document.getElementById('filtros')!, estado.filtros, {
    alCambiar: (f) => {
      aplicarFiltros(mapa, f);
      // La lista se repinta con el mismo predicado. Que el mapa y la lista
      // discrepen sería peor que no tener filtros: alguien vería una tarjeta
      // de un incendio que no está en el mapa y no sabría a cuál creer.
      refrescarLista();
    },
  });
  pintarLeyenda();

  window.setInterval(() => {
    void refrescar();
  }, REFRESCO_MS);

  // La latencia envejece mientras la pestaña está abierta: si no se repinta,
  // el panel diría "hace 1 min" durante una hora.
  window.setInterval(() => pintarLatencia(estado.manifiesto), 30_000);

  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') deseleccionar();
  });
}

// --- datos ------------------------------------------------------------------

async function cargarDatos(): Promise<void> {
  try {
    estado.manifiesto = await cargarManifiesto();
  } catch {
    estado.manifiesto = null;
    // RF-F-13: sin manifiesto no se inventa ninguna cifra. El mapa sigue
    // navegable y la banda dice exactamente qué ha pasado.
    pintarBanda(
      '<strong>No se han podido cargar los datos.</strong> El mapa sigue siendo ' +
        'navegable, pero no se muestra ningún incendio ni antigüedad. ' +
        'Ante una emergencia, 112.',
      'error',
    );
    pintarLatencia(null);
    pintarResumen(null);
    pintarFuentes(null);
    return;
  }

  estado.salud = await cargarSalud();

  pintarLatencia(estado.manifiesto);
  pintarResumen(estado.manifiesto);
  pintarFuentes(estado.salud);
  pintarAtribucion(estado.salud);
  actualizarBanda();

  try {
    estado.incidentes = await cargarIncidentes();
  } catch {
    estado.incidentes = null;
  }
}

function actualizarBanda(): void {
  const m = estado.manifiesto;
  if (!m) return;

  if (m.demo) {
    pintarBanda(
      `<strong>Datos de demostración.</strong> ${
        m.demo_reason ?? 'No corresponden a incendios reales.'
      } Ante una emergencia, 112.`,
      'demo',
    );
    return;
  }

  if (m.degraded) {
    pintarBanda(
      `<strong>Información incompleta.</strong> ${
        m.degraded_reason ?? 'Alguna fuente no está respondiendo.'
      } Lo que se muestra puede no reflejar la situación actual.`,
      'aviso',
    );
    return;
  }

  pintarBanda(null);
}

async function refrescar(): Promise<void> {
  await cargarDatos();
  const mapa = estado.mapa;
  if (!mapa || !estado.incidentes) return;

  const fuente = mapa.getSource(FUENTE_INCIDENTES) as maplibregl.GeoJSONSource | undefined;
  fuente?.setData(estado.incidentes as GeoJSON.FeatureCollection);
  refrescarLista();
}

// --- capas ------------------------------------------------------------------

function montarCapas(mapa: MapaGL): void {
  if (estado.incidentes) {
    mapa.addSource(FUENTE_INCIDENTES, {
      type: 'geojson',
      data: estado.incidentes as GeoJSON.FeatureCollection,
    });
    anadirCapasIncidentes(mapa);
  }

  void montarCapaDiferida(mapa, 'hotspots');
}

/**
 * RF-F-11 · cada capa opcional carga su GeoJSON **solo al activarse**.
 *
 * Los hotspots son 3 MB y los perímetros 1,5 MB: traerlos en el arranque se
 * comería el presupuesto de 900 KB de RNF-02 sin que nadie los haya pedido.
 */
async function montarCapaDiferida(mapa: MapaGL, capa: string): Promise<void> {
  const ya = {
    hotspots: () => mapa.getSource(FUENTE_HOTSPOTS),
    perimetros: () => mapa.getSource(FUENTE_PERIMETROS),
    viento: () => mapa.getSource(FUENTE_VIENTO),
    aire: () => mapa.getSource(FUENTE_AIRE),
    trafico: () => mapa.getSource(FUENTE_TRAFICO),
  }[capa];
  if (ya?.()) return;

  if (capa === 'hotspots') {
    const datos = await cargarGeoJson('hotspots.geojson');
    if (!datos || !mapa.getStyle()) return;
    mapa.addSource(FUENTE_HOTSPOTS, { type: 'geojson', data: datos });
    anadirCapaHotspots(mapa);
  }

  if (capa === 'perimetros') {
    const datos = await cargarGeoJson('perimeters.geojson');
    if (!datos) return;
    mapa.addSource(FUENTE_PERIMETROS, { type: 'geojson', data: datos });
    anadirCapasPerimetros(mapa);
  }

  if (capa === 'viento') {
    const datos = await cargarGeoJson('wind.geojson');
    if (!datos) return;
    mapa.addSource(FUENTE_VIENTO, { type: 'geojson', data: datos });
    anadirCapaViento(mapa);
  }

  if (capa === 'aire') {
    const datos = await cargarGeoJson('aire.geojson');
    if (!datos) return;
    mapa.addSource(FUENTE_AIRE, { type: 'geojson', data: datos });
    anadirCapaAire(mapa);
  }

  if (capa === 'trafico') {
    const datos = await cargarGeoJson('trafico.geojson');
    if (!datos) return;
    mapa.addSource(FUENTE_TRAFICO, { type: 'geojson', data: datos });
    anadirCapasTrafico(mapa);
  }
}

function alternarCapa(mapa: MapaGL, capa: string, activa: boolean): void {
  estado.capas[capa] = activa;
  const visibilidad = activa ? 'visible' : 'none';
  const ids: Record<string, string[]> = {
    hotspots: [CAPA_HOTSPOTS],
    perimetros: [CAPA_PERIMETRO_EFFIS, CAPA_PERIMETRO_ESTIMADO],
    viento: [CAPA_VIENTO],
    aire: [CAPA_AIRE],
    trafico: [CAPA_TRAFICO, CAPA_TRAFICO_INCENDIO],
  };

  const aplicar = () => {
    for (const id of ids[capa] ?? []) {
      if (mapa.getLayer(id)) mapa.setLayoutProperty(id, 'visibility', visibilidad);
    }
    // Una capa recién montada nace sin filtro: hay que aplicárselo o
    // aparecerían hotspots de 3 días con el período puesto en 1.
    aplicarFiltros(mapa, estado.filtros);
    pintarLeyenda();
  };

  if (activa) {
    void montarCapaDiferida(mapa, capa).then(aplicar);
  } else {
    aplicar();
  }
}

// --- interacción ------------------------------------------------------------

function conectarInteraccion(mapa: MapaGL): void {
  mapa.on('click', CAPA_INCIDENTES, (ev) => {
    const rasgo = ev.features?.[0];
    if (rasgo) seleccionar(rasgo.properties as PropiedadesIncidente);
  });

  mapa.on('mouseenter', CAPA_INCIDENTES, () => {
    mapa.getCanvas().style.cursor = 'pointer';
  });
  mapa.on('mouseleave', CAPA_INCIDENTES, () => {
    mapa.getCanvas().style.cursor = '';
  });

  // Pulsar fuera de cualquier incidente cierra la ficha.
  mapa.on('click', (ev) => {
    const encima = mapa.queryRenderedFeatures(ev.point, { layers: [CAPA_INCIDENTES] });
    if (encima.length === 0) deseleccionar();
  });
}

function seleccionar(p: PropiedadesIncidente): void {
  estado.seleccionado = p.id;
  resaltar(p.id);
  abrirFicha(p, deseleccionar);
  sincronizarUrl(estado.mapa, p.id);
}

function deseleccionar(): void {
  estado.seleccionado = null;
  resaltar(null);
  cerrarFicha();
  sincronizarUrl(estado.mapa, null);
}

function resaltar(id: string | null): void {
  const mapa = estado.mapa;
  if (!mapa?.getLayer(CAPA_RESALTE)) return;
  mapa.setFilter(CAPA_RESALTE, ['==', ['get', 'id'], id ?? '']);
}

function refrescarLista(): void {
  const mapa = estado.mapa;
  if (!mapa || !estado.incidentes) {
    pintarLista([], manejadoresLista());
    return;
  }

  const limites = mapa.getBounds();
  const visibles = estado.incidentes.features
    .filter((f) => {
      const c = f.geometry?.coordinates;
      if (!c || !limites.contains([c[0], c[1]])) return false;
      return pasaElFiltro(f.properties, estado.filtros);
    })
    .map((f) => f.properties);

  pintarLista(visibles, manejadoresLista());
}

/** Sin mapa la ficha sigue siendo útil: solo desaparece el centrado. */
function manejadoresSinMapa() {
  return {
    alPulsar: (id: string) => {
      const rasgo = estado.incidentes?.features.find((f) => f.properties.id === id);
      if (rasgo) abrirFicha(rasgo.properties, cerrarFicha);
    },
    alEntrar: () => {},
    alSalir: () => {},
  };
}

function manejadoresLista() {
  return {
    alPulsar: (id: string) => {
      const rasgo = estado.incidentes?.features.find((f) => f.properties.id === id);
      if (!rasgo) return;
      estado.mapa?.flyTo({
        center: rasgo.geometry.coordinates as [number, number],
        zoom: Math.max(estado.mapa.getZoom(), 10),
        duration: prefiereMenosMovimiento() ? 0 : 900,
      });
      seleccionar(rasgo.properties);
    },
    alEntrar: (id: string) => resaltar(id),
    alSalir: () => resaltar(estado.seleccionado),
  };
}

// --- estado en la URL · RF-F-02 --------------------------------------------

function vistaDesdeUrl(): { center: [number, number]; zoom: number } {
  const p = new URLSearchParams(location.search);
  const lat = Number(p.get('lat'));
  const lon = Number(p.get('lon'));
  const zoom = Number(p.get('zoom'));

  if (Number.isFinite(lat) && Number.isFinite(lon) && p.get('lat') && p.get('lon')) {
    return {
      center: [lon, lat],
      zoom: Number.isFinite(zoom) && zoom > 0 ? zoom : 10,
    };
  }
  return VISTA_ESPANA;
}

function sincronizarUrl(mapa: MapaGL | null, id?: string | null): void {
  if (!mapa) return;
  const centro = mapa.getCenter();
  const p = new URLSearchParams();
  p.set('lat', centro.lat.toFixed(4));
  p.set('lon', centro.lng.toFixed(4));
  p.set('zoom', mapa.getZoom().toFixed(2));

  const seleccion = id === undefined ? estado.seleccionado : id;
  if (seleccion) p.set('id', seleccion);

  // `replaceState` y no `pushState`: cada arrastre del mapa no puede añadir una
  // entrada al historial, o el botón atrás se vuelve inutilizable.
  history.replaceState(null, '', `${location.pathname}?${p.toString()}`);
}

/** Enlace profundo: al cargar con `?id=`, centrar y abrir la ficha. */
function abrirDesdeUrl(): void {
  const id = ID_INICIAL;
  if (!id || !estado.incidentes) return;

  const rasgo = estado.incidentes.features.find((f) => f.properties.id === id);
  if (!rasgo) {
    pintarBanda(
      'El incendio enlazado ya no aparece en los datos actuales. Puede haberse ' +
        'extinguido o haber dejado de detectarse.',
      'aviso',
    );
    return;
  }

  estado.mapa?.jumpTo({
    center: rasgo.geometry.coordinates as [number, number],
    zoom: 11,
  });
  seleccionar(rasgo.properties);
}

// --- controles de la barra lateral -----------------------------------------

function estiloGuardado(): ClaveEstilo {
  // `localStorage` solo para preferencias de interfaz, nunca para datos de
  // incendios (prohibición explícita de la sección 2.2).
  try {
    const v = localStorage.getItem(CLAVE_ESTILO);
    return esClaveEstilo(v) ? v : ESTILO_POR_DEFECTO;
  } catch {
    return ESTILO_POR_DEFECTO;
  }
}

function construirSelectorEstilo(mapa: MapaGL): void {
  const nodo = document.getElementById('selector-estilo')!;
  const actual = estiloGuardado();

  nodo.innerHTML = Object.entries(ESTILOS)
    .map(
      ([clave, { nombre }]) =>
        `<button type="button" role="radio" data-estilo="${clave}"
           aria-checked="${clave === actual}">${nombre}</button>`,
    )
    .join('');

  for (const boton of nodo.querySelectorAll<HTMLButtonElement>('button')) {
    boton.addEventListener('click', () => {
      const clave = boton.dataset.estilo as ClaveEstilo;
      try {
        localStorage.setItem(CLAVE_ESTILO, clave);
      } catch {
        /* modo privado: la preferencia no persiste, el mapa sí cambia */
      }
      mapa.setStyle(ESTILOS[clave].estilo);
      // `setStyle` descarta las capas propias: hay que rehacerlas.
      mapa.once('styledata', () => {
        montarCapas(mapa);
        conectarInteraccion(mapa);
        for (const [capa, activa] of Object.entries(estado.capas)) {
          if (activa) alternarCapa(mapa, capa, true);
        }
        aplicarFiltros(mapa, estado.filtros);
        resaltar(estado.seleccionado);
      });
      for (const otro of nodo.querySelectorAll('button')) {
        otro.setAttribute('aria-checked', String(otro === boton));
      }
    });
  }
}

function construirConmutadores(mapa: MapaGL): void {
  const nodo = document.getElementById('conmutadores')!;
  const capas: Array<[string, string]> = [
    ['hotspots', 'Focos satelitales'],
    ['perimetros', 'Perímetros'],
    ['viento', 'Viento'],
    ['aire', 'Calidad del aire'],
    ['trafico', 'Carreteras cortadas'],
  ];

  nodo.innerHTML = capas
    .map(
      ([clave, etiqueta]) =>
        `<button type="button" class="conmutador" data-capa="${clave}"
           aria-pressed="${estado.capas[clave] ?? false}">${etiqueta}</button>`,
    )
    .join('');

  for (const boton of nodo.querySelectorAll<HTMLButtonElement>('button')) {
    boton.addEventListener('click', () => {
      const capa = boton.dataset.capa!;
      const activa = boton.getAttribute('aria-pressed') !== 'true';
      boton.setAttribute('aria-pressed', String(activa));
      alternarCapa(mapa, capa, activa);
    });
  }
}

function pintarLeyenda(): void {
  const nodo = document.getElementById('leyenda')!;
  nodo.hidden = false;
  nodo.innerHTML = `
    <h3>Leyenda</h3>
    <div class="leyenda__fila">
      <span class="leyenda__muestra" style="background:#ffe08a"></span> Intensidad baja
    </div>
    <div class="leyenda__fila">
      <span class="leyenda__muestra" style="background:#f05a28"></span> Intensidad alta
    </div>
    <div class="leyenda__fila">
      <span class="leyenda__muestra" style="background:#c81e1e"></span> Intensidad extrema
    </div>
    <div class="leyenda__fila">
      <span class="leyenda__muestra leyenda__muestra--anillo"></span>
      Margen de posición de la fuente
    </div>
    <div class="leyenda__fila leyenda__fila--texto">
      Borde grueso: confirmado por parte oficial
    </div>
    ${
      estado.capas.viento
        ? `<div class="leyenda__fila leyenda__fila--texto">
             Las flechas apuntan <b>hacia donde sopla</b> el viento, no de dónde viene
           </div>`
        : ''
    }
    ${
      estado.capas.trafico
        ? `<div class="leyenda__fila">
             <span class="leyenda__muestra" style="background:#ffe08a;border:2px solid #c81e1e"></span>
             Corte <b>por incendio</b>, según la DGT
           </div>
           <div class="leyenda__fila">
             <span class="leyenda__muestra" style="background:#7d9199"></span>
             Otros cortes de carretera
           </div>`
        : ''
    }
    ${
      estado.capas.aire
        ? `<div class="leyenda__fila leyenda__fila--texto">
             Calidad del aire: índice europeo. Un valor alto <b>no implica</b> que
             el humo venga del incendio más cercano
           </div>`
        : ''
    }`;
}

function anadirIndicadorZoom(mapa: MapaGL): void {
  // RF-F-01 pide el nivel de zoom visible. MapLibre no lo trae de serie.
  class IndicadorZoom implements maplibregl.IControl {
    private nodo!: HTMLDivElement;
    onAdd(m: MapaGL): HTMLElement {
      this.nodo = document.createElement('div');
      this.nodo.className = 'maplibregl-ctrl maplibregl-ctrl-group nivel-zoom';
      const pintar = () => {
        this.nodo.textContent = `z${m.getZoom().toFixed(1)}`;
      };
      m.on('zoom', pintar);
      pintar();
      return this.nodo;
    }
    onRemove(): void {
      this.nodo.remove();
    }
  }
  mapa.addControl(new IndicadorZoom(), 'top-right');
}

function prefiereMenosMovimiento(): boolean {
  return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
}

void arrancar();
