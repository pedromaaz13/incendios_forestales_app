import { test } from '@playwright/test';

import { VIEWPORTS, abrir, conManifiesto } from './ayuda';

/**
 * Generador de las evidencias de la sección 9.2.
 *
 * «Un requisito sin captura no se considera entregado». Este fichero produce
 * los ficheros con el nombre exacto de la tabla, en `docs/evidencias/hito-2/`.
 *
 * Todas las capturas salen de datos de fixture y con el mapa base bloqueado, de
 * modo que dos ejecuciones distintas den la misma imagen. Es lo que permite la
 * comparación de regresión visual que pide 9.3: si cambia más del 0,5 % de los
 * píxeles, es por el código, no por qué esté ardiendo hoy.
 */

const DIR = '../docs/evidencias/hito-2';

function ruta(nombre: string): string {
  return `${DIR}/${nombre}`;
}

// --- 01 · vista inicial en los tres anchos ---------------------------------

for (const vp of VIEWPORTS) {
  test(`captura 01 · inicial ${vp.name}`, async ({ page }) => {
    await page.setViewportSize({ width: vp.width, height: vp.height });
    await abrir(page);
    await page.waitForTimeout(900);
    await page.screenshot({ path: ruta(`01-inicial-${vp.name}.png`), fullPage: false });
  });
}

// --- 02-04 · los tres umbrales de latencia ---------------------------------

const UMBRALES = [
  { fichero: '02-latencia-verde.png', segundos: 1500 },
  { fichero: '03-latencia-ambar.png', segundos: 8340 },
  { fichero: '04-latencia-roja.png', segundos: 21600 },
];

for (const { fichero, segundos } of UMBRALES) {
  test(`captura · ${fichero}`, async ({ page }) => {
    await conManifiesto(page, {
      worst_data_age_seconds: segundos,
      data_age_seconds: { firms_viirs: segundos },
    });
    await abrir(page);
    // Solo la cabecera: es donde vive el requisito y recortar hace la
    // comparación de regresión mucho menos ruidosa.
    await page.locator('.cabecera').screenshot({ path: ruta(fichero) });
  });
}

// --- 05 · banda de degradado -----------------------------------------------

test('captura · 05-degradado.png', async ({ page }) => {
  await conManifiesto(page, {
    demo: false,
    degraded: true,
    degraded_reason:
      'fuentes críticas sin datos recientes: INFOCAM / FIDIAS (error); ' +
      'el dato más antiguo tiene 5.2 h (umbral: 4 h)',
  });
  await abrir(page);
  await page.waitForTimeout(600);
  await page.screenshot({ path: ruta('05-degradado.png') });
});

// --- 06 · panel de estado de fuentes ---------------------------------------

test('captura · 06-fuentes-panel.png', async ({ page }) => {
  await abrir(page);
  await page.waitForTimeout(600);
  // El fixture lleva INFOCAM en error a propósito: la captura interesante es
  // esa, no ocho filas verdes que no demuestran que el estado se distinga.
  await page.locator('.panel--izq').screenshot({ path: ruta('06-fuentes-panel.png') });
});

// --- 07-09 · fichas e incertidumbre ----------------------------------------

test('captura · 07-incidente-ambos.png', async ({ page }) => {
  await abrir(page);
  const tarjetas = page.locator('.tarjeta');
  for (let i = 0; i < (await tarjetas.count()); i++) {
    const texto = await tarjetas.nth(i).textContent();
    if (texto?.includes('Satélite y')) {
      await tarjetas.nth(i).click();
      break;
    }
  }
  await page.waitForTimeout(900);
  await page.screenshot({ path: ruta('07-incidente-ambos.png') });
});

test('captura · 08-incidente-oficial.png', async ({ page }) => {
  await abrir(page);
  const tarjetas = page.locator('.tarjeta');
  for (let i = 0; i < (await tarjetas.count()); i++) {
    const texto = await tarjetas.nth(i).textContent();
    if (texto?.includes('sin detección satelital')) {
      await tarjetas.nth(i).click();
      break;
    }
  }
  await page.waitForTimeout(900);
  await page.screenshot({ path: ruta('08-incidente-oficial.png') });
});

test('captura · 09-incertidumbre-infocam.png', async ({ page }) => {
  // El anillo de ±6 km sobre un parte de INFOCAM huérfano. Es la diferencia
  // clave del producto frente a la competencia (RF-F-03).
  await abrir(page, '/?id=off_infocam_CLM-FID-5530&zoom=10');
  await page.waitForTimeout(1400);
  await page.screenshot({ path: ruta('09-incertidumbre-infocam.png') });
});

// --- 10 · hotspots a zoom alto ---------------------------------------------

test('captura · 10-hotspots-zoom.png', async ({ page }) => {
  await abrir(page, '/?lat=40.25&lon=-6.60&zoom=11');
  await page.waitForTimeout(1400);
  await page.screenshot({ path: ruta('10-hotspots-zoom.png') });
});

// --- 11 · filtro de origen oficial -----------------------------------------

test('captura · 11-filtros-oficial.png', async ({ page }) => {
  await abrir(page);
  await page.locator('[data-grupo="origen"] [data-valor="oficial"]').click();
  await page.waitForTimeout(900);
  await page.screenshot({ path: ruta('11-filtros-oficial.png') });
});

// --- 13-14 · capas opcionales ----------------------------------------------

test('captura · 13-viento.png', async ({ page }) => {
  await abrir(page, '/?lat=40.25&lon=-6.60&zoom=8');
  await page.locator('[data-capa="viento"]').click();
  await page.waitForTimeout(1600);
  await page.screenshot({ path: ruta('13-viento.png') });
});

test('captura · 14-perimetros.png', async ({ page }) => {
  await abrir(page, '/?lat=40.25&lon=-6.60&zoom=11');
  await page.locator('[data-capa="perimetros"]').click();
  await page.waitForTimeout(1600);
  await page.screenshot({ path: ruta('14-perimetros.png') });
});

// --- 15-16 · estados vacío y de error --------------------------------------

test('captura · 15-vacio.png', async ({ page }) => {
  await abrir(page, '/?lat=36.0&lon=-3.5&zoom=11');
  await page.waitForTimeout(900);
  await page.screenshot({ path: ruta('15-vacio.png') });
});

test('captura · 16-error-red.png', async ({ page }) => {
  await page.route('**/live/manifest.json', (r) => r.fulfill({ status: 500, body: 'error' }));
  await abrir(page);
  await page.waitForTimeout(900);
  await page.screenshot({ path: ruta('16-error-red.png') });
});

// --- 17 · sin WebGL ---------------------------------------------------------

test('captura · 17-sin-webgl.png', async ({ page }) => {
  // Se anula `getContext` antes de que cargue el bundle, que es exactamente lo
  // que ve un navegador con la aceleración desactivada.
  await page.addInitScript(() => {
    const original = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (tipo: string, ...resto: unknown[]) {
      if (String(tipo).includes('webgl')) return null;
      // @ts-expect-error firma variádica del original
      return original.call(this, tipo, ...resto);
    };
  });
  await abrir(page);
  await page.waitForTimeout(700);
  await page.screenshot({ path: ruta('17-sin-webgl.png') });
});

test('captura · 18-avisos-aemet.png', async ({ page }) => {
  // Vista de conjunto de la península: los avisos cubren comarcas enteras, así
  // que a zoom 8 solo se vería un borde y no se entendería la capa.
  await abrir(page, '/?lat=40.0&lon=-3.5&zoom=5.4');
  await page.locator('[data-capa="avisos"]').click();
  await page.waitForTimeout(1800);
  await page.screenshot({ path: ruta('18-avisos-aemet.png') });
});

test('captura · 19-ficha-contexto.png', async ({ page }) => {
  // Sierra de Gata: el incendio de la demo que cae bajo aviso naranja y tiene
  // cortes de carretera cerca, así que la ficha enseña las tres líneas.
  await abrir(page, '/?lat=40.25&lon=-6.60&zoom=9');
  await page.locator('.tarjeta').first().click();
  await page.waitForTimeout(900);
  await page.screenshot({ path: ruta('19-ficha-contexto.png') });
});
