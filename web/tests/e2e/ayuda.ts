import type { Page } from '@playwright/test';

/**
 * Utilidades compartidas por los escenarios.
 *
 * La regla que gobierna este fichero: **ninguna prueba toca la red externa**.
 * Las teselas del mapa base se abortan siempre, porque son el único recurso que
 * el visor pide fuera y su latencia haría las capturas irreproducibles.
 */

export const VIEWPORTS = [
  { name: 'movil', width: 390, height: 844 },
  { name: 'tablet', width: 834, height: 1112 },
  { name: 'escritorio', width: 1680, height: 1050 },
] as const;

// Un proveedor que falte aquí no da error: la prueba simplemente empieza a
// tocar la red y se vuelve lenta e irreproducible. Pasó al añadir el mapa
// sobrio, que es el estilo por defecto y sirve desde CARTO: 22 escenarios
// fallaron de golpe. Al añadir un estilo nuevo, su dominio va también aquí.
const TESELAS = [
  '**://tile.openstreetmap.org/**',
  '**://server.arcgisonline.com/**',
  '**://tile.opentopomap.org/**',
  '**://*.basemaps.cartocdn.com/**',
];

export async function bloquearTeselas(page: Page): Promise<void> {
  for (const patron of TESELAS) {
    await page.route(patron, (ruta) => ruta.abort());
  }
}

/** Sustituye el manifiesto por uno construido a medida para el escenario. */
export async function conManifiesto(page: Page, cambios: Record<string, unknown>): Promise<void> {
  await page.route('**/live/manifest.json', async (ruta) => {
    const original = await ruta.fetch();
    const base = await original.json();
    await ruta.fulfill({ json: { ...base, ...cambios } });
  });
}

export async function abrir(page: Page, ruta = '/'): Promise<void> {
  await bloquearTeselas(page);
  await page.goto(ruta, { waitUntil: 'domcontentloaded' });
  // La lista se pinta cuando los datos han llegado; es la señal más fiable de
  // que la aplicación está lista, mejor que un tiempo fijo.
  await page.waitForFunction(
    () => document.querySelectorAll('.tarjeta, .vacio').length > 0,
    null,
    { timeout: 15_000 },
  );
  await capaDeFocosLista(page);
}

/**
 * Espera a que la capa de focos exista **y tenga filtro**.
 *
 * Se monta de forma diferida —hay que descargar su GeoJSON— y su filtro se
 * aplica en el `then` de ese montaje. Las pruebas lo suplían con
 * `waitForTimeout` de 1,5 s, que basta en una máquina descargada y no basta con
 * la suite entera corriendo: tres pruebas distintas fallaban en cada ejecución,
 * siempre de la capa de focos, y las tres pasaban aisladas.
 *
 * Un tiempo fijo no es una espera, es una apuesta. Esto espera a la condición.
 *
 * No falla si la capa no llega: hay escenarios sin focos —el mapa vacío, el
 * fallo de red— donde no montarla es el comportamiento correcto.
 */
export async function capaDeFocosLista(page: Page, timeout = 8_000): Promise<void> {
  await page
    .waitForFunction(
      () => {
        const mapa = (window as never as { __mapa?: { getLayer: (id: string) => unknown;
          getFilter: (id: string) => unknown } }).__mapa;
        if (!mapa?.getLayer('hotspots-punto')) return false;
        return mapa.getFilter('hotspots-punto') != null;
      },
      null,
      { timeout },
    )
    .catch(() => undefined);
}


/**
 * Espera a que una capa **haya pintado** algo.
 *
 * `queryRenderedFeatures` consulta lo que hay en pantalla, no lo que hay en la
 * fuente: devuelve vacío hasta que MapLibre termina de dibujar el fotograma. Un
 * `waitForTimeout` fijo funciona en una máquina descargada y falla con la suite
 * entera corriendo, que es cuando el ordenador va justo.
 */
export async function capaConFeatures(
  page: Page,
  capa: string,
  timeout = 8_000,
): Promise<void> {
  await page.waitForFunction(
    (id) => {
      const mapa = (window as never as {
        __mapa?: { getLayer: (i: string) => unknown; queryRenderedFeatures: (o: unknown) => unknown[] };
      }).__mapa;
      if (!mapa?.getLayer(id)) return false;
      return mapa.queryRenderedFeatures({ layers: [id] }).length > 0;
    },
    capa,
    { timeout },
  );
}
