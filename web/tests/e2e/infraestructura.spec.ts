import type maplibregl from 'maplibre-gl';
import { expect, test } from '@playwright/test';

import { abrir } from './ayuda';

/**
 * Infraestructura crítica · líneas eléctricas y ferrocarril.
 *
 * Lo que más importa comprobar aquí no es que se pinten, es que **no se
 * descarguen si nadie las enciende**: son 1,5 y 3,5 MB, varias veces el
 * presupuesto de carga inicial (RNF-02).
 */

test('las capas pesadas no se descargan en la carga inicial', async ({ page }) => {
  const pedidas: string[] = [];
  page.on('request', (r) => {
    if (/electricas|ferrocarril/.test(r.url())) pedidas.push(r.url());
  });

  await abrir(page);

  expect(pedidas, 'nadie las ha encendido: no deben pedirse').toHaveLength(0);
});

test('se descargan y se pintan al activarlas', async ({ page }) => {
  await abrir(page);

  await page.click('[data-capa="electricas"]');
  await expect(page.locator('[data-capa="electricas"]')).toHaveAttribute('aria-pressed', 'true');

  await expect
    .poll(
      async () =>
        page.evaluate(() =>
          Boolean(
            (window as never as { __mapa?: maplibregl.Map }).__mapa?.getLayer('electricas-linea'),
          ),
        ),
      { timeout: 20000 },
    )
    .toBe(true);
});

test('la leyenda explica de dónde salen y de cuándo son', async ({ page }) => {
  await abrir(page);
  await page.click('[data-capa="ferrocarril"]');

  const leyenda = page.locator('.leyenda');
  await expect(leyenda).toContainText('OpenStreetMap');
  // Es una foto, no tiempo real: decirlo evita que se lea como el estado
  // actual de la red, igual que con CORINE 2018.
  await expect(leyenda).toContainText('no el estado actual');
});

test('a escala nacional solo se ve la red troncal', async ({ page }) => {
  await abrir(page);
  await page.click('[data-capa="electricas"]');

  const visibles = await expect
    .poll(
      async () =>
        page.evaluate(() => {
          const m = (window as never as { __mapa?: maplibregl.Map }).__mapa;
          if (!m?.getLayer('electricas-linea')) return -1;
          return m.queryRenderedFeatures({ layers: ['electricas-linea'] }).length;
        }),
      { timeout: 20000 },
    )
    .not.toBe(-1)
    .then(() =>
      page.evaluate(() => {
        const m = (window as never as { __mapa?: maplibregl.Map }).__mapa!;
        const f = m.queryRenderedFeatures({ layers: ['electricas-linea'] });
        return f.map((x) => Number(x.properties?.kv ?? 0));
      }),
    );

  // Con las 8.584 líneas pintadas a la vez, la red tapa los incendios —que son
  // el dato principal—. De lejos solo debe verse la troncal.
  expect(visibles.length).toBeGreaterThan(0);
  expect(Math.min(...visibles), 'a zoom nacional no deben salir los 132 kV').toBeGreaterThanOrEqual(400);
});
