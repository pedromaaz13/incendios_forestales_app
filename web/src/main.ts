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

import {
  cargarEstatico,
  cargarGeoJson,
  cargarIncidentes,
  cargarManifiesto,
  cargarSalud,
} from './datos';
import {
  CAPA_AIRE,
  CAPA_GRUPOS,
  CAPA_AVISOS,
  CAPA_AVISOS_BORDE,
  CAPA_SUELO,
  CAPA_TRAFICO,
  CAPA_TRAFICO_INCENDIO,
  CAPA_HOTSPOTS,
  ZOOM_HOTSPOTS,
  CAPA_INCIDENTES,
  CAPA_PERIMETRO_EFFIS,
  CAPA_PERIMETRO_ESTIMADO,
  CAPA_RESALTE,
  CAPA_VIENTO,
  FUENTE_HOTSPOTS,
  FUENTE_INCIDENTES,
  FUENTE_PERIMETROS,
  FUENTE_AIRE,
  FUENTE_AVISOS,
  FUENTE_SUELO,
  FUENTE_TRAFICO,
  FUENTE_VIENTO,
  anadirCapaAire,
  anadirCapasGrupos,
  hacerRuidososLosErrores,
  registrarCifrasDeGrupo,
  anadirCapaSuelo,
  anadirCapasAvisos,
  anadirCapasTrafico,
  anadirCapaHotspots,
  anadirCapaViento,
  anadirCapasIncidentes,
  anadirCapasPerimetros,
  pintarActivos,
  FUENTE_ELECTRICAS,
  FUENTE_FERROCARRIL,
  anadirCapaElectricas,
  anadirCapaFerrocarril,
  CAPA_ELECTRICAS,
  CAPA_ELECTRICAS_RESTO,
  CAPA_FERROCARRIL,} from './map/capas';
import { ESTILOS, ESTILO_POR_DEFECTO, esClaveEstilo, type ClaveEstilo } from './map/estilos';
import { CAPA_VIENTO_ANIMADO, CapaVientoAnimado } from './map/viento-animado';
import { agruparPorDia, pintarEvolutivo, type DiaEvolutivo } from './ui/evolutivo';
import {
  type Cruce,
  aplicarCruce,
  construirCruces,
  pintarResultado,
} from './ui/cruces';
import { construirBuscador } from './ui/buscador';
import {
  type Activo,
  CERCA_KM,
  type Exposicion,
  calcularExposicion,
  construirActivos,
  guardar,
  pintarExposicion,
} from './ui/activos';
import { abrirFicha, cerrarFicha, registrarFocos } from './ui/ficha';
import {
  FILTROS_INICIALES,
  aplicar as aplicarFiltros,
  construirControles as construirFiltros,
  avisarSensoresSegunZoom,
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
  dias: DiaEvolutivo[];
  diaElegido: string | null;
  /** Cruce activo, o `null` si se ve todo. */
  cruce: Cruce | null;
  /** Puntos que ha subido el usuario. Nunca salen del navegador. */
  activos: Activo[] | null;
  /** Umbral de cercanía elegido, en kilómetros. */
  activosCercaKm: number;
}

const estado: Estado = {
  mapa: null,
  manifiesto: null,
  salud: null,
  incidentes: null,
  seleccionado: null,
  capas: {
    hotspots: true, perimetros: false, viento: false,
    aire: false, trafico: false, avisos: false, suelo: false,
    electricas: false, ferrocarril: false,
  },
  filtros: { ...FILTROS_INICIALES, sensores: new Set(FILTROS_INICIALES.sensores) },
  dias: [],
  diaElegido: null,
  cruce: null,
  activos: null,
  activosCercaKm: CERCA_KM,
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
  hacerRuidososLosErrores(mapa);
  // Enganche para las pruebas de extremo a extremo: permite consultar qué
  // capas están montadas y qué features hay pintadas sin depender del DOM.
  (window as unknown as { __mapa?: MapaGL }).__mapa = mapa;

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
      aplicarFiltros(mapa, estado.filtros, estado.diaElegido);
      refrescarLista();
      abrirDesdeUrl();
    });
  });

  mapa.on('moveend', () => {
    sincronizarUrl(mapa);
    refrescarLista();
  });

  // El aviso del filtro de sensor se recalcula con el zoom, no solo al mover:
  // así aparece y desaparece justo al cruzar el umbral en que salen los focos.
  mapa.on('zoom', () => avisarSensoresSegunZoom(mapa.getZoom(), ZOOM_HOTSPOTS));

  construirSelectorEstilo(mapa);
  construirCruces(document.getElementById('cruces')!, {
    alElegir: (cruce) => {
      estado.cruce = cruce;
      refrescarLista();
    },
  });
  construirBuscador(document.getElementById('buscador')!, {
    alElegir: (nucleo) => {
      mapa.flyTo({
        center: [nucleo.lon, nucleo.lat],
        zoom: Math.max(mapa.getZoom(), 11),
        duration: prefiereMenosMovimiento() ? 0 : 900,
      });
    },
  });
  construirActivos(document.getElementById('activos')!, {
    alCambiar: (activos) => {
      estado.activos = activos;
      guardar(activos, estado.activosCercaKm);
      refrescarActivos();
    },
    alCambiarDistancia: (km) => {
      estado.activosCercaKm = km;
      guardar(estado.activos, km);
      refrescarActivos();
    },
    alElegir: (activo) => {
      mapa.flyTo({
        center: [activo.lon, activo.lat],
        zoom: Math.max(mapa.getZoom(), 11),
        duration: prefiereMenosMovimiento() ? 0 : 900,
      });
    },
  });
  construirConmutadores(mapa);
  construirFiltros(document.getElementById('filtros')!, estado.filtros, {
    alCambiar: (f) => {
      aplicarFiltros(mapa, f, estado.diaElegido);
      // La lista se repinta con el mismo predicado. Que el mapa y la lista
      // discrepen sería peor que no tener filtros: alguien vería una tarjeta
      // de un incendio que no está en el mapa y no sabría a cuál creer.
      refrescarLista();
    },
  });
  // Después de construir los controles: antes, el nodo del aviso no existe y
  // la llamada no hacía nada. Al abrir directamente en zoom bajo el evento
  // `zoom` tampoco dispara, así que hace falta esta primera pasada.
  avisarSensoresSegunZoom(mapa.getZoom(), ZOOM_HOTSPOTS);
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
  refrescarActivos();
}

// --- capas ------------------------------------------------------------------

function montarCapas(mapa: MapaGL): void {
  if (estado.incidentes) {
    mapa.addSource(FUENTE_INCIDENTES, {
      type: 'geojson',
      data: estado.incidentes as GeoJSON.FeatureCollection,
      cluster: true,
      // A partir de zoom 9 no se agrupa: es el mismo umbral en el que aparecen
      // los hotspots, así que al llegar ahí se ve el detalle completo de una
      // zona en lugar de una mezcla de globos y puntos.
      clusterMaxZoom: 9,
      clusterRadius: 45,
      clusterProperties: {
        // El grupo hereda la gravedad de su peor incendio, no la media.
        peor: ['max', ['match', ['get', 'intensity'],
          'baja', 1, 'media', 2, 'alta', 3, 'extrema', 4, 1]],
      },
    });
    registrarCifrasDeGrupo(mapa);
    anadirCapasGrupos(mapa);
    anadirCapasIncidentes(mapa);
  }

  // El `then` no es opcional. La capa de focos se monta de forma asíncrona —hay
  // que descargar su GeoJSON— así que `aplicarFiltros` de más abajo corre antes
  // de que exista y **la capa nacía sin filtro ninguno**.
  //
  // Consecuencia medida en producción: FIRMS se pide con 3 días de margen, y
  // 579 de los 1.182 focos publicados tenían más de 24 h. Se pintaban todos
  // mientras el control decía «1 día». No fallaba nada visible: el mapa
  // enseñaba más focos de los que decía, y nadie compara 600 puntos a ojo.
  void montarCapaDiferida(mapa, 'hotspots').then(() => {
    aplicarFiltros(mapa, estado.filtros, estado.diaElegido);
  });
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
    // Se comprueba la capa y no la fuente: al apagar el viento se retira la
    // capa de partículas pero la fuente permanece, y mirar la fuente haría
    // creer que ya está montado.
    viento: () => mapa.getLayer(CAPA_VIENTO_ANIMADO),
    aire: () => mapa.getSource(FUENTE_AIRE),
    trafico: () => mapa.getSource(FUENTE_TRAFICO),
    avisos: () => mapa.getSource(FUENTE_AVISOS),
    suelo: () => mapa.getSource(FUENTE_SUELO),
    electricas: () => mapa.getSource(FUENTE_ELECTRICAS),
    ferrocarril: () => mapa.getSource(FUENTE_FERROCARRIL),
  }[capa];
  if (ya?.()) return;

  if (capa === 'hotspots') {
    const datos = await cargarGeoJson('hotspots.geojson');
    if (!datos || !mapa.getStyle()) return;
    mapa.addSource(FUENTE_HOTSPOTS, { type: 'geojson', data: datos });
    anadirCapaHotspots(mapa);

    // El evolutivo sale de las fechas de los propios focos: la ventana de 3
    // días ya viene descargada y no hace falta pedir nada más.
    estado.dias = agruparPorDia(
      datos.features.map((f) => f.properties as { acq_dt: string }),
    );
    refrescarEvolutivo(mapa);

    // Los mismos focos, indexados por incendio, para el evolutivo de la ficha.
    // Se agrupa una vez al cargar y no en cada apertura: son decenas de miles
    // de rasgos y recorrerlos con cada clic se nota en móvil.
    const porIncendio = new Map<string, Array<{ acq_dt: string }>>();
    for (const f of datos.features) {
      const props = f.properties as { fire_id?: string; acq_dt: string } | null;
      if (!props?.fire_id) continue;
      const lista = porIncendio.get(props.fire_id);
      if (lista) lista.push(props);
      else porIncendio.set(props.fire_id, [props]);
    }
    registrarFocos(porIncendio);
  }

  // Son 1,5 y 3,5 MB: solo se piden al activarlas. Cargarlas de entrada se
  // comería varias veces el presupuesto de la carga inicial (RNF-02) para algo
  // que la mayoría de visitantes no va a encender.
  if (capa === 'electricas') {
    const datos = await cargarEstatico<GeoJSON.FeatureCollection>('electricas.geojson');
    if (!datos || !mapa.getStyle()) return;
    mapa.addSource(FUENTE_ELECTRICAS, { type: 'geojson', data: datos });
    anadirCapaElectricas(mapa);
  }

  if (capa === 'ferrocarril') {
    const datos = await cargarEstatico<GeoJSON.FeatureCollection>('ferrocarril.geojson');
    if (!datos || !mapa.getStyle()) return;
    mapa.addSource(FUENTE_FERROCARRIL, { type: 'geojson', data: datos });
    anadirCapaFerrocarril(mapa);
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
    if (!mapa.getSource(FUENTE_VIENTO)) {
      mapa.addSource(FUENTE_VIENTO, { type: 'geojson', data: datos });
      anadirCapaViento(mapa);
    }
    // Las partículas van encima de las flechas: las flechas dan el valor
    // puntual y medible, las partículas el sentido del flujo. Con
    // `prefers-reduced-motion` la capa se añade sin animar.
    if (!mapa.getLayer(CAPA_VIENTO_ANIMADO)) {
      mapa.addLayer(new CapaVientoAnimado(datos, !prefiereMenosMovimiento()));
    }
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

  if (capa === 'suelo') {
    // No hay GeoJSON que descargar: es un servicio de teselas. Se monta por
    // debajo de los focos para no taparlos, que son el objeto del visor.
    anadirCapaSuelo(mapa, CAPA_HOTSPOTS);
    return;
  }

  if (capa === 'avisos') {
    const datos = await cargarGeoJson('avisos.geojson');
    if (!datos) return;
    mapa.addSource(FUENTE_AVISOS, { type: 'geojson', data: datos });
    anadirCapasAvisos(mapa);
  }
}

function alternarCapa(mapa: MapaGL, capa: string, activa: boolean): void {
  estado.capas[capa] = activa;
  const visibilidad = activa ? 'visible' : 'none';
  const ids: Record<string, string[]> = {
    hotspots: [CAPA_HOTSPOTS],
    perimetros: [CAPA_PERIMETRO_EFFIS, CAPA_PERIMETRO_ESTIMADO],
    viento: [CAPA_VIENTO, CAPA_VIENTO_ANIMADO],
    aire: [CAPA_AIRE],
    trafico: [CAPA_TRAFICO, CAPA_TRAFICO_INCENDIO],
    avisos: [CAPA_AVISOS, CAPA_AVISOS_BORDE],
    suelo: [CAPA_SUELO],
    electricas: [CAPA_ELECTRICAS, CAPA_ELECTRICAS_RESTO],
    ferrocarril: [CAPA_FERROCARRIL],
  };

  const aplicar = () => {
    for (const id of ids[capa] ?? []) {
      if (!mapa.getLayer(id)) continue;
      // La capa de partículas es `custom` y pinta en su propio lienzo, así que
      // `visibility` no la afecta: hay que retirarla del mapa.
      if (id === CAPA_VIENTO_ANIMADO) {
        if (!activa) mapa.removeLayer(id);
        continue;
      }
      mapa.setLayoutProperty(id, 'visibility', visibilidad);
    }
    // Una capa recién montada nace sin filtro: hay que aplicárselo o
    // aparecerían hotspots de 3 días con el período puesto en 1.
    aplicarFiltros(mapa, estado.filtros, estado.diaElegido);
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
  // Pulsar un grupo acerca hasta el zoom en el que ese grupo se abre, en vez
  // de un salto fijo: así el gesto siempre revela lo que hay dentro.
  mapa.on('click', CAPA_GRUPOS, (ev) => {
    const grupo = ev.features?.[0];
    const id = grupo?.properties?.cluster_id;
    if (id === undefined) return;

    const fuente = mapa.getSource(FUENTE_INCIDENTES) as maplibregl.GeoJSONSource;
    void fuente.getClusterExpansionZoom(id).then((zoom) => {
      mapa.easeTo({
        center: (grupo!.geometry as GeoJSON.Point).coordinates as [number, number],
        zoom: zoom + 0.2,
        duration: prefiereMenosMovimiento() ? 0 : 600,
      });
    });
  });

  for (const capa of [CAPA_GRUPOS, CAPA_INCIDENTES]) {
    mapa.on('mouseenter', capa, () => {
      mapa.getCanvas().style.cursor = 'pointer';
    });
    mapa.on('mouseleave', capa, () => {
      mapa.getCanvas().style.cursor = '';
    });
  }

  mapa.on('click', CAPA_INCIDENTES, (ev) => {
    const rasgo = ev.features?.[0];
    if (rasgo) seleccionar(rasgo.properties as PropiedadesIncidente);
  });

  // Pulsar fuera de cualquier incidente cierra la ficha.
  mapa.on('click', (ev) => {
    const capas = [CAPA_INCIDENTES, CAPA_GRUPOS].filter((c) => mapa.getLayer(c));
    const encima = mapa.queryRenderedFeatures(ev.point, { layers: capas });
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
  mapa.setFilter(CAPA_RESALTE, [
    'all',
    ['!', ['has', 'point_count']],
    ['==', ['get', 'id'], id ?? ''],
  ]);
}

/** Traduce la exposición al nivel de color que entiende la capa del mapa. */
function nivelDe(e: Exposicion): string {
  if (e.distanciaKm === null || e.distanciaKm > estado.activosCercaKm) return 'lejos';
  if (e.aSotavento === null) return 'duda';
  return e.aSotavento ? 'alta' : 'media';
}

function pintarActivosEnMapa(exposiciones: Exposicion[]): void {
  if (!estado.mapa) return;
  pintarActivos(
    estado.mapa,
    exposiciones.map((e) => ({
      nombre: e.activo.nombre,
      lon: e.activo.lon,
      lat: e.activo.lat,
      nivel: nivelDe(e),
    })),
  );
}

/**
 * Recalcula la exposición de los activos del usuario.
 *
 * Se llama también al refrescar los datos: si aparece un incendio nuevo cerca
 * de una nave, el panel tiene que enterarse sin recargar la página.
 */
function refrescarActivos(): void {
  if (!estado.activos) {
    pintarExposicion([]);
    pintarActivosEnMapa([]);
    return;
  }
  const exposiciones = calcularExposicion(
    estado.activos,
    estado.incidentes?.features ?? [],
  );
  pintarExposicion(exposiciones, estado.activosCercaKm);
  pintarActivosEnMapa(exposiciones);
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

  // El cruce se aplica **después** del encuadre y de los filtros: la pregunta
  // es siempre sobre lo que se está viendo, no sobre toda España. Si no, el
  // recuento diría «3 de 49» mientras en pantalla hay cinco incendios.
  if (!estado.cruce) {
    pintarResultado(visibles.length, null);
    aplicarFiltros(mapa, estado.filtros, estado.diaElegido, null);
    pintarLista(visibles, manejadoresLista());
    return;
  }

  const resultado = aplicarCruce(visibles, estado.cruce);
  pintarResultado(visibles.length, resultado);
  aplicarFiltros(mapa, estado.filtros, estado.diaElegido, resultado.cumplen);
  pintarLista(
    visibles.filter((p) => resultado.cumplen.includes(p.id)),
    manejadoresLista(),
  );
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
        aplicarFiltros(mapa, estado.filtros, estado.diaElegido);
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
    ['avisos', 'Avisos de AEMET'],
    ['suelo', 'Tipo de terreno'],
    ['electricas', 'Líneas eléctricas'],
    ['ferrocarril', 'Ferrocarril'],
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

/**
 * Repinta el evolutivo y aplica el día elegido a las capas.
 *
 * Elegir un día filtra los focos a ese día, no los incidentes: un incendio
 * puede arder varios días y ocultarlo porque su última detección no cae en el
 * día elegido daría a entender que no existía, que es falso.
 */
function refrescarEvolutivo(mapa: MapaGL): void {
  const nodo = document.getElementById('evolutivo');
  if (!nodo) return;

  pintarEvolutivo(nodo, estado.dias, estado.diaElegido, {
    alElegirDia: (dia) => {
      estado.diaElegido = dia;
      aplicarFiltros(mapa, estado.filtros, dia);
      refrescarEvolutivo(mapa);
      refrescarLista();
    },
  });
}

function pintarLeyenda(): void {
  const nodo = document.getElementById('leyenda')!;
  nodo.hidden = false;
  nodo.innerHTML = `
    <h3>Leyenda</h3>
    <p class="leyenda__grupo">Intensidad de la anomalía térmica</p>
    <div class="leyenda__fila">
      <span class="leyenda__muestra leyenda__muestra--punto" style="--c:#ffd93d"></span>
      Baja <span class="leyenda__nota">FRP bajo o pocos focos</span>
    </div>
    <div class="leyenda__fila">
      <span class="leyenda__muestra leyenda__muestra--punto" style="--c:#f05a28"></span>
      Alta
    </div>
    <div class="leyenda__fila">
      <span class="leyenda__muestra leyenda__muestra--punto" style="--c:#c81e1e"></span>
      Extrema
    </div>
    <!-- La intensidad es potencia radiativa detectada, no gravedad del suceso.
         Se dice explícitamente porque el color rojo invita a leerlo como
         "peligro" y un incendio pequeño junto a casas es más grave que uno
         extremo en despoblado. -->
    <p class="leyenda__aviso">
      Mide el <b>calor detectado</b> desde el satélite, no la gravedad ni la
      cercanía a población.
    </p>

    <p class="leyenda__grupo">Cómo se ha confirmado</p>
    <div class="leyenda__fila">
      <span class="leyenda__muestra leyenda__muestra--oficial"></span>
      Confirmado por <b>parte oficial</b> (borde grueso)
    </div>
    <div class="leyenda__fila">
      <span class="leyenda__muestra leyenda__muestra--punto" style="--c:#f05a28"></span>
      Solo <b>detección satelital</b>
    </div>
    <div class="leyenda__fila">
      <span class="leyenda__muestra leyenda__muestra--anillo"></span>
      Margen de posición que declara la fuente
    </div>
    <div class="leyenda__fila">
      <span class="leyenda__muestra leyenda__muestra--grupo">7</span>
      Varios incendios juntos: la cifra los cuenta. Acerca para separarlos
    </div>
    ${
      estado.capas.electricas || estado.capas.ferrocarril
        ? `<p class="leyenda__grupo">Infraestructura</p>
           ${
             estado.capas.electricas
               ? `<div class="leyenda__fila">
                    <span class="leyenda__muestra leyenda__muestra--linea" style="--c:#e8a33d"></span>
                    Naranjas <span class="leyenda__nota">líneas de alta tensión · a más grosor, más kV</span>
                  </div>`
               : ''
           }
           ${
             estado.capas.ferrocarril
               ? `<div class="leyenda__fila">
                    <span class="leyenda__muestra leyenda__muestra--via"></span>
                    Discontinua gris <span class="leyenda__nota">ferrocarril</span>
                  </div>`
               : ''
           }
           <!-- Fechar la descarga importa por lo mismo que en CORINE: es una
                foto, no tiempo real, y una línea puede haberse construido
                después. -->
           <p class="leyenda__aviso">
             Datos de <b>OpenStreetMap</b> (ODbL), descargados el 04-08-2026.
             Solo se muestra la red de <b>transporte</b>: las líneas de
             distribución de baja tensión no están. Es una foto de esa fecha,
             no el estado actual de la red.
           </p>`
        : ''
    }
    ${
      estado.capas.suelo
        ? `<p class="leyenda__grupo">Tipo de terreno</p>
           <div class="leyenda__fila">
             <span class="leyenda__muestra leyenda__muestra--zona" style="--c:#3a9c3a"></span>
             Verdes <span class="leyenda__nota">monte, matorral y pastizal</span>
           </div>
           <div class="leyenda__fila">
             <span class="leyenda__muestra leyenda__muestra--zona" style="--c:#e8d24a"></span>
             Ocres <span class="leyenda__nota">cultivos y prados</span>
           </div>
           <div class="leyenda__fila">
             <span class="leyenda__muestra leyenda__muestra--zona" style="--c:#d24b4b"></span>
             Rojos <span class="leyenda__nota">urbano e industrial</span>
           </div>
           <div class="leyenda__fila">
             <span class="leyenda__muestra leyenda__muestra--zona" style="--c:#66a3d2"></span>
             Azules <span class="leyenda__nota">agua y humedales</span>
           </div>
           <div class="leyenda__fila">
             <span class="leyenda__muestra leyenda__muestra--zona" style="--c:#111111"></span>
             Negro <span class="leyenda__nota">superficie ya quemada en 2018</span>
           </div>
           <!-- El negro sorprende la primera vez y parece un fallo de carga. No
                lo es: CORINE tiene una clase 334 «zonas quemadas» y su paleta la
                pinta en negro. En Sierra de Gata son las cicatrices del incendio
                de 2015. Explicarlo convierte una mancha rara en el dato más
                interesante de la capa: dónde ya ardió. -->
           <!-- La paleta original de CORINE tiene 44 tonos y aquí se conservan
                tal cual: el servidor entrega el PNG ya pintado. Agrupar por
                familia de color es lo que hace legible el mapa sin tocar el
                ráster, porque el ojo ya agrupa así. La clase exacta de cada
                incendio está en su ficha. -->
           <p class="leyenda__aviso">
             Cartografía <b>CORINE 2018</b>: es una foto de ese año, no del
             estado actual del terreno. Sirve para saber si un incendio está en
             monte o en cultivo, no para medir superficies. Las manchas negras
             son terreno que <b>ya había ardido</b> cuando se levantó el mapa.
             Al acercarse mucho <b>se ve borroso</b>: CORINE cartografía a partir
             de 25 ha y no tiene más detalle que ese.
           </p>`
        : ''
    }
    ${
      estado.capas.avisos
        ? `<p class="leyenda__grupo">Avisos oficiales de AEMET</p>
           <div class="leyenda__fila">
             <span class="leyenda__muestra leyenda__muestra--zona" style="--c:#f1c40f"></span>
             Amarillo <span class="leyenda__nota">riesgo para actividades concretas</span>
           </div>
           <div class="leyenda__fila">
             <span class="leyenda__muestra leyenda__muestra--zona" style="--c:#e67e22"></span>
             Naranja <span class="leyenda__nota">riesgo meteorológico importante</span>
           </div>
           <div class="leyenda__fila">
             <span class="leyenda__muestra leyenda__muestra--zona" style="--c:#c0392b"></span>
             Rojo <span class="leyenda__nota">riesgo extremo</span>
           </div>
           <!-- Se dice de quién es el aviso y a qué se refiere. Son los colores
                oficiales de Meteoalerta, los mismos de los partes del tiempo, y
                un aviso naranja de calor no es un incendio: es la condición que
                lo favorece. Confundirlos sería leer el mapa al revés. -->
           <p class="leyenda__aviso">
             Los publica <b>AEMET</b>, no este visor, y avisan del
             <b>tiempo previsto</b> —calor, viento, tormenta—, no de que haya
             fuego en esa zona.
           </p>`
        : ''
    }
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
