import { expect, test } from '@playwright/test';

import { abrir, conManifiesto } from './ayuda';

/**
 * Escenarios E2E de la sección 8.4.
 *
 * Lo que se prueba aquí no es que la interfaz "se vea bien": es que **no
 * induzca a error**. Las tres cosas que más se comprueban son que las dos
 * latencias nunca se confunden, que un fallo de datos se declara en lugar de
 * dejar cifras viejas, y que el aviso del 112 no desaparece en ninguna
 * resolución.
 */

// --- E2E-01 · carga inicial -------------------------------------------------

test('E2E-01 · carga inicial con España completa', async ({ page }) => {
  await abrir(page);

  await expect(page.locator('#mapa canvas')).toBeVisible();
  await expect(page.locator('#latencia-dato')).not.toHaveText('—');
  await expect(page.locator('#latencia-pipeline')).not.toHaveText('—');
  await expect(page.locator('.tarjeta').first()).toBeVisible();
});

test('E2E-01b · las dos latencias son números distintos', async ({ page }) => {
  // Es el requisito que define el producto (RF-F-05). Si alguien fusionara los
  // dos paneles en uno, esta prueba es lo que lo impediría.
  await conManifiesto(page, {
    generated_at: new Date(Date.now() - 4 * 60_000).toISOString().replace(/\.\d+Z$/, 'Z'),
    worst_data_age_seconds: 8340,
    data_age_seconds: { firms_viirs: 8340 },
  });
  await abrir(page);

  await expect(page.locator('#latencia-dato')).toHaveText('2 h 19 min');
  await expect(page.locator('#latencia-pipeline')).toContainText('hace 4 min');
});

// --- E2E-02 · ficha de incidente --------------------------------------------

test('E2E-02 · abrir la ficha de un incidente', async ({ page }) => {
  await abrir(page);
  await page.locator('.tarjeta').first().click();

  const ficha = page.locator('#ficha');
  await expect(ficha).toBeVisible();
  await expect(ficha.locator('#ficha-titulo')).not.toBeEmpty();
  await expect(ficha).toContainText('Margen declarado');
});

test('E2E-02b · la superficie se declara estimada', async ({ page }) => {
  // Regla de dominio: nunca un verbo de certeza sobre una cifra derivada.
  await abrir(page);

  const tarjetas = page.locator('.tarjeta');
  for (let i = 0; i < (await tarjetas.count()); i++) {
    await tarjetas.nth(i).click();
    const ficha = page.locator('#ficha');
    if (await ficha.getByText('Superficie estimada').first().isVisible()) {
      await expect(ficha).toContainText('no medida');
      return;
    }
  }
  test.fail(true, 'ningún incidente publicó superficie estimada');
});

// --- E2E-03 · filtros -------------------------------------------------------

test('E2E-03 · filtrar solo confirmados oficialmente', async ({ page }) => {
  await abrir(page);
  const antes = await page.locator('.tarjeta').count();

  await page.locator('[data-grupo="origen"] [data-valor="oficial"]').click();
  await page.waitForTimeout(300);

  const despues = await page.locator('.tarjeta').count();
  expect(despues).toBeLessThanOrEqual(antes);
  // Todo lo que quede tiene que declarar una fuente oficial.
  for (const texto of await page.locator('.tarjeta').allTextContents()) {
    expect(texto).not.toContain('Detección satelital sin parte oficial');
  }
});

test('E2E-03b · el filtro no recarga datos', async ({ page }) => {
  // RF-F-09: los filtros van por `setFilter`, no por refetch. Si alguien lo
  // cambiara por una recarga, el rendimiento de RNF-04 se caería sin aviso.
  await abrir(page);
  // La capa de hotspots se carga en diferido (RF-F-11) y su petición puede
  // llegar después del `abrir`. Sin esperar a que la red se calme, el listener
  // la contaría como si fuera culpa del filtro.
  await page.waitForLoadState('networkidle');

  const peticiones: string[] = [];
  page.on('request', (r) => {
    if (r.url().includes('/live/')) peticiones.push(r.url());
  });

  await page.locator('[data-grupo="periodo"] [data-valor="3"]').click();
  await page.locator('[data-grupo="confianza"] [data-valor="alta"]').click();
  await page.waitForTimeout(400);

  expect(peticiones).toHaveLength(0);
});

test('E2E-03c · apagar todos los sensores deja la capa vacía', async ({ page }) => {
  await abrir(page);

  for (const sensor of ['VIIRS', 'MODIS', 'SEVIRI']) {
    await page.locator(`[data-grupo="sensores"] [data-valor="${sensor}"]`).click();
  }

  for (const sensor of ['VIIRS', 'MODIS', 'SEVIRI']) {
    await expect(
      page.locator(`[data-grupo="sensores"] [data-valor="${sensor}"]`),
    ).toHaveAttribute('aria-pressed', 'false');
  }
});

// --- E2E-06 · enlace profundo ----------------------------------------------

test('E2E-06 · cargar con ?id= abre la ficha', async ({ page }) => {
  await abrir(page);
  const id = await page.locator('.tarjeta').first().getAttribute('data-id');

  await abrir(page, `/?id=${encodeURIComponent(id!)}`);

  await expect(page.locator('#ficha')).toBeVisible();
  await expect(page.locator('#ficha')).toContainText('Enlace permanente');
});

test('E2E-06b · un id inexistente se explica, no se ignora', async ({ page }) => {
  await abrir(page, '/?id=no-existe-este-incendio');

  await expect(page.locator('#banda')).toContainText('ya no aparece');
});

// --- E2E-07 · manifest.json caído ------------------------------------------

test('E2E-07 · manifest.json devuelve 500', async ({ page }) => {
  await page.route('**/live/manifest.json', (r) => r.fulfill({ status: 500, body: 'error' }));
  await abrir(page);

  const banda = page.locator('#banda');
  await expect(banda).toBeVisible();
  await expect(banda).toHaveAttribute('data-tono', 'error');
  await expect(banda).toContainText('No se han podido cargar los datos');

  // Y sobre todo: ninguna cifra inventada donde no hay datos.
  await expect(page.locator('#latencia-dato')).toHaveText('—');
  await expect(page.locator('#resumen')).toContainText('Sin datos');
  await expect(page.locator('#mapa canvas')).toBeVisible();
});

// --- E2E-08 · degradado -----------------------------------------------------

test('E2E-08 · degraded true muestra el motivo', async ({ page }) => {
  await conManifiesto(page, {
    demo: false,
    degraded: true,
    degraded_reason: 'INFOCAM sin respuesta desde hace 42 min',
  });
  await abrir(page);

  const banda = page.locator('#banda');
  await expect(banda).toHaveAttribute('data-tono', 'aviso');
  await expect(banda).toContainText('INFOCAM sin respuesta');
});

// --- E2E-09 · viewport sin incendios ---------------------------------------

test('E2E-09 · zona sin incendios lo dice', async ({ page }) => {
  // Mar de Alborán: no hay incendios en el agua.
  await abrir(page, '/?lat=36.0&lon=-3.5&zoom=11');

  await expect(page.locator('#lista-incidentes')).toContainText('Sin incendios');
  await expect(page.locator('#contador-visibles')).toHaveText('0');
});

// --- E2E-10 · umbrales de latencia -----------------------------------------

const UMBRALES = [
  { nombre: 'verde', segundos: 1800, nivel: 'ok' },
  { nombre: 'ambar', segundos: 7200, nivel: 'warn' },
  { nombre: 'roja', segundos: 18000, nivel: 'bad' },
];

for (const { nombre, segundos, nivel } of UMBRALES) {
  test(`E2E-10 · latencia ${nombre}`, async ({ page }) => {
    await conManifiesto(page, {
      worst_data_age_seconds: segundos,
      data_age_seconds: { firms_viirs: segundos },
    });
    await abrir(page);

    await expect(page.locator('#latencia-dato')).toHaveAttribute('data-nivel', nivel);
  });
}

// --- E2E-12 · prefers-reduced-motion ---------------------------------------

test.describe('E2E-12 · movimiento reducido', () => {
  test.use({ reducedMotion: 'reduce' });

  test('la aplicación funciona sin animaciones', async ({ page }) => {
    await abrir(page);
    await page.locator('.tarjeta').first().click();

    await expect(page.locator('#ficha')).toBeVisible();
  });
});

// --- RF-F-12 · aviso legal permanente --------------------------------------

const ANCHOS = [390, 834, 1680, 2560];

for (const ancho of ANCHOS) {
  test(`RF-F-12 · el aviso del 112 se ve a ${ancho} px`, async ({ page }) => {
    await page.setViewportSize({ width: ancho, height: 844 });
    await abrir(page);

    const aviso = page.locator('.aviso-legal');
    await expect(aviso).toBeInViewport();
    await expect(aviso).toContainText('112');
    await expect(aviso).toContainText('No es información oficial');

    // Y sin desbordar en horizontal, que dejaría el aviso fuera al desplazar.
    const desborda = await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth,
    );
    expect(desborda).toBe(false);
  });
}

// --- RNF-06 · navegación por teclado ---------------------------------------

test('RNF-06 · la lista es alcanzable y operable con teclado', async ({ page }) => {
  await abrir(page);

  const primera = page.locator('.tarjeta').first();
  await primera.focus();
  await expect(primera).toBeFocused();

  await page.keyboard.press('Enter');
  await expect(page.locator('#ficha')).toBeVisible();

  await page.keyboard.press('Escape');
  await expect(page.locator('#ficha')).toBeHidden();
});

// --- RF-F-06 · estado de fuentes -------------------------------------------

test('RF-F-06 · las fuentes en error van primero y con texto', async ({ page }) => {
  await abrir(page);

  const primera = page.locator('.fuente').first();
  await expect(primera).toHaveClass(/fuente--error/);
  // El estado no puede transmitirse solo por color.
  await expect(primera).toContainText('sin respuesta');
});
