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
            (window as never as { __mapa?: maplibregl.Map }).__mapa?.getLayer('electricas-troncal'),
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

  // Se espera a que la troncal esté pintada, no solo a que la capa exista:
  // montar la fuente y renderizar las teselas son momentos distintos, y en CI
  // el segundo llega más tarde que en local.
  await expect
    .poll(
      async () =>
        page.evaluate(() => {
          const m = (window as never as { __mapa?: maplibregl.Map }).__mapa;
          if (!m?.getLayer('electricas-troncal')) return 0;
          return m.queryRenderedFeatures({ layers: ['electricas-troncal'] }).length;
        }),
      { timeout: 25000 },
    )
    .toBeGreaterThan(0);

  // El resto de la red tiene minzoom 7.5: a escala nacional (z5.1) no puede
  // haber nada pintado, o la red taparía los incendios.
  const resto = await page.evaluate(() => {
    const m = (window as never as { __mapa?: maplibregl.Map }).__mapa!;
    return m.getLayer('electricas-resto')
      ? m.queryRenderedFeatures({ layers: ['electricas-resto'] }).length
      : -1;
  });
  expect(resto, 'la capa de resto debe existir').not.toBe(-1);
  expect(resto, 'a z5 no deben pintarse los 132 kV').toBe(0);
});
