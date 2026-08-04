import { expect, test } from '@playwright/test';

import { abrir } from './ayuda';

/**
 * El visor en un teléfono.
 *
 * Esto no es un caso secundario: quien busca «incendio cerca de mi pueblo» lo
 * hace desde el móvil, con prisa. Y hasta el 04-08-2026 desde un teléfono el
 * visor era **solo el mapa** — el panel quedaba en x = −283 y no existía ningún
 * control para traerlo, así que el buscador, los filtros, las capas, «Mis
 * activos» y el estado de las fuentes eran inalcanzables.
 *
 * La causa fue silenciosa: el CSS ya contemplaba `data-abierto`, pero nadie lo
 * ponía. Nada fallaba; simplemente no había puerta.
 */

test.use({ viewport: { width: 390, height: 844 } });

test('el panel se puede abrir desde un teléfono', async ({ page }) => {
  await abrir(page);

  const panel = page.locator('#panel-izq');
  const boton = page.locator('#panel-boton');

  await expect(boton).toBeVisible();
  // Cerrado: fuera de la pantalla por la izquierda.
  expect(await panel.evaluate((n) => n.getBoundingClientRect().x)).toBeLessThan(0);

  await boton.click();

  // Abierto: pegado al borde. Sin esto el visor no tiene panel en móvil.
  await expect
    .poll(async () => panel.evaluate((n) => Math.round(n.getBoundingClientRect().x)))
    .toBe(0);
  await expect(boton).toHaveAttribute('aria-expanded', 'true');
});

test('con el panel abierto se llega a todo lo que hemos montado', async ({ page }) => {
  await abrir(page);
  await page.click('#panel-boton');

  // La lista concreta importa: es justo lo que estaba fuera de alcance.
  for (const selector of ['#buscador-campo', '#filtros', '#cruces', '#activos', '#conmutadores']) {
    await expect(page.locator(selector), `${selector} debe alcanzarse en móvil`).toBeVisible();
  }
});

test('el botón no tapa el contenido del panel', async ({ page }) => {
  await abrir(page);
  await page.click('#panel-boton');

  // Abierto, el botón se aparta al borde derecho del cajón. Encima del panel se
  // comía la primera fuente de la lista.
  const [botonX, panelAncho] = await page.evaluate(() => [
    document.getElementById('panel-boton')!.getBoundingClientRect().x,
    document.getElementById('panel-izq')!.getBoundingClientRect().width,
  ]);
  expect(botonX).toBeGreaterThanOrEqual(panelAncho);
});

test('tocar fuera cierra el cajón', async ({ page }) => {
  await abrir(page);
  await page.click('#panel-boton');
  await expect(page.locator('#panel-velo')).toBeVisible();

  // La posición es relativa al velo, no a la ventana. Con y=600 el punto caía
  // sobre el aviso legal del pie, que es permanente por RF-F-12 e intercepta el
  // clic: la prueba fallaba por su coordenada, no por el cajón.
  await page.locator('#panel-velo').click({ position: { x: 340, y: 260 } });

  // En una pantalla estrecha el cajón tapa el mapa: obligar a buscar el botón
  // otra vez para volver a ver dónde arde es la fricción que no puede tener.
  await expect
    .poll(async () => page.locator('#panel-izq').evaluate((n) => n.getBoundingClientRect().x))
    .toBeLessThan(0);
});

test('elegir una capa cierra el cajón para poder verla', async ({ page }) => {
  await abrir(page);
  await page.click('#panel-boton');

  await page.click('[data-capa="suelo"]');

  // Casi todo lo del panel tiene su efecto en el mapa; dejarlo tapado obligaría
  // a cerrarlo a mano cada vez.
  await expect
    .poll(async () => page.locator('#panel-izq').evaluate((n) => n.getBoundingClientRect().x))
    .toBeLessThan(0);
});

test('Escape cierra el cajón y devuelve el foco al botón', async ({ page }) => {
  await abrir(page);
  await page.click('#panel-boton');

  await page.keyboard.press('Escape');

  await expect(page.locator('#panel-boton')).toHaveAttribute('aria-expanded', 'false');
  await expect(page.locator('#panel-boton')).toBeFocused();
});

test('el aviso del 112 sigue visible con el panel abierto', async ({ page }) => {
  await abrir(page);
  await page.click('#panel-boton');

  // RF-F-12: permanente, en toda resolución y en todo estado. Un cajón que lo
  // tapara sería una regresión de la regla que gobierna este proyecto.
  await expect(page.locator('.aviso-legal')).toBeVisible();
  await expect(page.locator('.aviso-legal')).toContainText('112');
});
