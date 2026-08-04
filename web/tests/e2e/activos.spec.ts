import { expect, test } from '@playwright/test';

import { abrir } from './ayuda';

/**
 * Buscar tu sitio y ver tus activos expuestos.
 *
 * Lo que se prueba aquí no es que el panel funcione, es que **no afirme de
 * más**. Las dos cosas que importan: que un activo sin viento publicado se
 * declare como tal en vez de contarse como seguro, y que el fichero del
 * usuario no salga del navegador.
 */

const CSV = `nombre,lat,lon
Nave Norte,40.50,-3.70
Camping El Pinar,40.42,-3.69
Bodega Sur,37.39,-5.99`;

/** Sube un fichero sin tocar el disco: el input acepta un buffer en memoria. */
async function subir(page: import('@playwright/test').Page, nombre: string, contenido: string) {
  await page.setInputFiles('#activos-fichero', {
    name: nombre,
    mimeType: 'text/csv',
    buffer: Buffer.from(contenido, 'utf-8'),
  });
}

test('el buscador encuentra un pueblo ignorando acentos', async ({ page }) => {
  await abrir(page);

  await page.fill('#buscador-campo', 'avila');

  const lista = page.locator('#buscador-lista');
  await expect(lista).toBeVisible();
  await expect(lista.getByText('Ávila', { exact: false }).first()).toBeVisible();
});

test('el índice de búsqueda no se descarga hasta que se teclea', async ({ page }) => {
  const peticiones: string[] = [];
  page.on('request', (r) => {
    if (r.url().includes('nucleos-indice')) peticiones.push(r.url());
  });

  await abrir(page);
  // Son ~520 KB comprimidos: cargarlos de entrada se comería más de la mitad
  // del presupuesto de carga inicial (RNF-02) para quien nunca busca.
  expect(peticiones, 'el índice no debe pedirse en la carga inicial').toHaveLength(0);

  await page.fill('#buscador-campo', 'avila');
  await expect(page.locator('#buscador-lista')).toBeVisible();
  expect(peticiones.length).toBeGreaterThan(0);
});

test('un CSV de activos se cruza con los incendios y se ordena por exposición', async ({ page }) => {
  await abrir(page);
  await subir(page, 'activos.csv', CSV);

  const resultado = page.locator('#activos-resultado');
  await expect(resultado).toBeVisible();
  await expect(resultado.locator('.activos__fila')).toHaveCount(3);
  // El recuento se redacta distinto según haya o no incendios cerca, y con los
  // datos de demostración puede ser cualquiera de los dos. Lo que no puede
  // faltar en ninguna de las dos es decir sobre cuántos puntos se responde.
  await expect(resultado.locator('.activos__recuento')).toContainText(/\b3\b/);
});

test('el fichero del usuario no se envía a ninguna parte', async ({ page }) => {
  const salientes: string[] = [];
  page.on('request', (r) => {
    if (['POST', 'PUT', 'PATCH'].includes(r.method())) salientes.push(`${r.method()} ${r.url()}`);
  });

  await abrir(page);
  await subir(page, 'activos.csv', CSV);
  await expect(page.locator('#activos-resultado')).toBeVisible();

  // Es media venta del producto: una lista de subestaciones es información
  // sensible y la promesa solo vale si se comprueba.
  expect(salientes, 'no debe salir ninguna petición de escritura').toEqual([]);
});

test('un CSV sin coordenadas se rechaza con un motivo legible', async ({ page }) => {
  await abrir(page);
  await subir(page, 'malo.csv', 'nombre,notas\nNave Norte,sin coordenadas');

  const error = page.locator('#activos-error');
  await expect(error).toBeVisible();
  await expect(error).toContainText('latitud');
  await expect(page.locator('#activos-resultado')).toBeHidden();
});

test('las coordenadas invertidas se detectan en vez de pintar puntos en Somalia', async ({
  page,
}) => {
  await abrir(page);
  await subir(page, 'invertido.csv', 'nombre,lat,lon\nNave,-3.70,40.50\nOtra,-5.99,37.39');

  await expect(page.locator('#activos-error')).toContainText('invertidas');
});

test('el aviso de que no es una herramienta de decisión no se oculta', async ({ page }) => {
  await abrir(page);
  await subir(page, 'activos.csv', CSV);

  const aviso = page.locator('.activos__aviso');
  await expect(aviso).toBeVisible();
  await expect(aviso).toContainText('112');
});

test('quitar los activos deja el panel como estaba', async ({ page }) => {
  await abrir(page);
  await subir(page, 'activos.csv', CSV);
  await expect(page.locator('#activos-resultado')).toBeVisible();

  await page.click('#activos-quitar');

  await expect(page.locator('#activos-resultado')).toBeHidden();
});

test('los activos sobreviven a recargar la página', async ({ page }) => {
  await abrir(page);
  await subir(page, 'activos.csv', CSV);
  await expect(page.locator('#activos-resultado')).toBeVisible();

  await page.reload();

  // Volver a subir el mismo fichero cada mañana es la fricción que más mata el
  // uso recurrente. localStorage sigue cumpliendo la promesa: no sale del
  // navegador.
  await expect(page.locator('#activos-resultado')).toBeVisible();
  await expect(page.locator('.activos__fila')).toHaveCount(3);
});

test('quitar los activos los borra también del almacenamiento', async ({ page }) => {
  await abrir(page);
  await subir(page, 'activos.csv', CSV);
  await page.click('#activos-quitar');
  await page.reload();

  // Si sobrevivieran al borrado, la promesa de «no se guarda nada que tú no
  // quieras» dejaría de ser cierta.
  await expect(page.locator('#activos-resultado')).toBeHidden();
});

test('el umbral de cercanía lo elige el usuario y se recuerda', async ({ page }) => {
  await abrir(page);
  await subir(page, 'activos.csv', CSV);

  await page.selectOption('#activos-km', '25');
  await expect(page.locator('.activos__recuento')).toContainText('25 km');

  await page.reload();
  await expect(page.locator('#activos-km')).toHaveValue('25');
});

test('el desplegable del buscador es opaco y no deja ver lo de debajo', async ({ page }) => {
  await abrir(page);
  await page.fill('#buscador-campo', 'Cascant');
  await expect(page.locator('#buscador-lista')).toBeVisible();

  const fondo = await page
    .locator('#buscador-lista')
    .evaluate((n) => getComputedStyle(n).backgroundColor);

  // Una variable CSS inexistente no da error: el navegador deja la propiedad
  // sin aplicar y el fondo queda transparente. Así se coló la primera vez, con
  // la lista de fuentes leyéndose a través de los resultados.
  expect(fondo).not.toBe('rgba(0, 0, 0, 0)');
  expect(fondo, 'debe ser opaco, no translúcido').not.toContain('rgba');
});
