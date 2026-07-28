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

const TESELAS = [
  '**://tile.openstreetmap.org/**',
  '**://server.arcgisonline.com/**',
  '**://tile.opentopomap.org/**',
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
}
