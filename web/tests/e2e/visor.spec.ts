import type maplibregl from 'maplibre-gl';
import { expect, test } from '@playwright/test';

import { abrir, capaConFeatures, conManifiesto } from './ayuda';

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
  // El estado de fuentes se inyecta en vez de usar el publicado: si hoy no hay
  // ninguna caída, el test pasaría sin comprobar nada, y el día que la haya
  // fallaría sin que nadie hubiera tocado el código.
  await page.route('**/live/sources.json', (ruta) =>
    ruta.fulfill({
      json: {
        generated_at: '2026-07-29T12:00:00Z',
        sources: [
          {
            id: 'ok', name: 'Aaa correcta', region: 'España', kind: 'satelite',
            critical: false, status: 'ok', last_success_at: '2026-07-29T11:58:00Z',
            age_seconds: 120, ttl_seconds: 600, records: 100, precision_m: 375,
            error: null, consecutive_failures: 0, attribution: '',
          },
          {
            id: 'roto', name: 'Zzz caída', region: 'España', kind: 'oficial',
            critical: false, status: 'error', last_success_at: null,
            age_seconds: null, ttl_seconds: 300, records: 0, precision_m: 500,
            error: 'HTTP 503 en el portal de origen', consecutive_failures: 3,
            attribution: '',
          },
        ],
      },
    }),
  );
  await abrir(page);

  // Las caídas van primero aunque alfabéticamente fueran las últimas.
  const primera = page.locator('.fuente').first();
  await expect(primera).toHaveClass(/fuente--error/);
  // El estado no puede transmitirse solo por color.
  await expect(primera).toContainText('sin respuesta');
});

// --- RF-P-07 · sin capa municipal ------------------------------------------

test('sin municipio la tarjeta dice dónde está, no solo que no lo sabe', async ({ page }) => {
  // Mientras no esté la capa del IGN todos los incidentes salen sin nombre.
  // "Ubicación por determinar" es honesto y completamente inútil: quien mira la
  // lista no puede saber si le pilla cerca. Las coordenadas sí lo dicen.
  await abrir(page);

  const textos = await page.locator('.tarjeta').allTextContents();
  const sinNombre = textos.filter((t) => t.includes('Ubicación por determinar'));

  for (const t of sinNombre) {
    expect(t).toMatch(/\d+\.\d{4}° [NS], \d+\.\d{4}° [EO]/);
  }
});

// --- agrupación numérica de incidentes -------------------------------------

test('los incidentes se agrupan con su número a zoom bajo', async ({ page }) => {
  // Matiz sobre RF-F-04: la prohibición del badge numérico es sobre hotspots,
  // donde un "638" mezcla incendios, quemas y falsos positivos. Los incidentes
  // ya están validados, así que un "3" sí es una afirmación sostenible.
  await abrir(page, '/?lat=40.2&lon=-4.0&zoom=6');
  await capaConFeatures(page, 'incidentes-grupo');

  const grupos = await page.evaluate(() =>
    (window as never as { __mapa?: maplibregl.Map }).__mapa
      ?.queryRenderedFeatures({ layers: ['incidentes-grupo'] })
      .map((g) => g.properties?.point_count),
  );

  expect(grupos?.length ?? 0).toBeGreaterThan(0);
  for (const n of grupos ?? []) expect(n).toBeGreaterThan(1);
});

test('los grupos se dispersan al acercar', async ({ page }) => {
  // El encuadre sale del propio dato, no de una coordenada escrita a mano.
  // La versión anterior apuntaba a un punto que tenía incendio en el dataset de
  // aquel día; al regenerar la demo el sitio se quedó vacío y el test falló sin
  // que hubiera ninguna regresión. Es la misma trampa que ya nos costó las
  // fechas congeladas.
  // La coordenada se lee del GeoJSON publicado y no del mapa: `querySourceFeatures`
  // solo devuelve lo que hay en las teselas ya cargadas, y a zoom inicial una
  // fuente agrupada puede no tener ni un punto suelto que devolver.
  const datos = await (await page.request.get('/live/incidents.geojson')).json();
  const centro = datos.features
    .map((f: GeoJSON.Feature) => (f.geometry as GeoJSON.Point).coordinates)
    // Canarias fuera: a zoom 11 sobre el Atlántico no hay nada más que mirar y
    // el encuadre no representa el caso que la prueba quiere comprobar.
    .find((c: number[]) => c[0] > -10 && c[0] < 4 && c[1] > 36 && c[1] < 44);
  expect(centro).toBeTruthy();

  await page.goto(`/?lat=${centro[1]}&lon=${centro[0]}&zoom=11`);
  await page.waitForTimeout(2000);

  const cuenta = await page.evaluate(() => {
    const m = (window as never as { __mapa?: maplibregl.Map }).__mapa;
    return {
      grupos: m?.queryRenderedFeatures({ layers: ['incidentes-grupo'] }).length ?? -1,
      sueltos: m?.queryRenderedFeatures({ layers: ['incidentes-simbolo'] }).length ?? -1,
    };
  });

  expect(cuenta.grupos).toBe(0);
  expect(cuenta.sueltos).toBeGreaterThan(0);
});

test('los hotspots nunca se agrupan', async ({ page }) => {
  // Esto sí lo prohíbe RF-F-04 sin matices, y es la mitad de la decisión.
  await abrir(page);
  const capas = await page.evaluate(
    () =>
      (window as never as { __mapa?: maplibregl.Map }).__mapa
        ?.getStyle()
        .layers.map((c) => c.id) ?? [],
  );

  expect(capas.filter((c) => c.includes('hotspot') && c.includes('grupo'))).toHaveLength(0);
});

// --- evolución diaria -------------------------------------------------------

test('el evolutivo marca el día en curso como incompleto', async ({ page }) => {
  // La última barra baja porque el día no ha terminado y los satélites polares
  // no han hecho todas sus pasadas. Leerlo como mejoría es el error fácil.
  await abrir(page);
  await page.waitForTimeout(1500);

  await expect(page.locator('.evolutivo__barra')).not.toHaveCount(0);
  await expect(page.locator('.evolutivo__pie')).toContainText('incompleta');
  await expect(
    page.locator('.evolutivo__barra[data-parcial="true"]'),
  ).not.toHaveCount(0);
});

test('pulsar una barra filtra los focos de ese día', async ({ page }) => {
  await abrir(page);
  await page.waitForTimeout(1500);

  const barra = page.locator('.evolutivo__barra').first();
  await barra.click();
  await expect(barra).toHaveAttribute('aria-pressed', 'true');

  // Volver a pulsarla deselecciona: sin eso no habría forma de ver todo otra vez.
  await barra.click();
  await expect(barra).toHaveAttribute('aria-pressed', 'false');
});

// --- capas: todas montan y se apagan ---------------------------------------

const CAPAS: Array<[string, string]> = [
  ['hotspots', 'hotspots-punto'],
  ['perimetros', 'perimetros-estimado'],
  ['viento', 'viento-flecha'],
  ['aire', 'aire-circulo'],
  ['trafico', 'trafico-corte'],
  ['avisos', 'avisos-relleno'],
];

for (const [conmutador, capa] of CAPAS) {
  test(`el conmutador de ${conmutador} monta y apaga su capa`, async ({ page }) => {
    await abrir(page);
    await page.waitForTimeout(1200);

    const boton = page.locator(`[data-capa="${conmutador}"]`);
    const encendida = (await boton.getAttribute('aria-pressed')) === 'true';
    if (encendida) await boton.click();

    await boton.click();
    await page.waitForTimeout(1800);

    await expect(boton).toHaveAttribute('aria-pressed', 'true');
    const montada = await page.evaluate(
      (id) => !!(window as never as { __mapa?: maplibregl.Map }).__mapa?.getLayer(id),
      capa,
    );
    expect(montada).toBe(true);
  });
}

test('el viento activa las partículas animadas', async ({ page }) => {
  await abrir(page);
  await page.locator('[data-capa="viento"]').click();
  await page.waitForTimeout(2200);

  const animado = await page.evaluate(() => ({
    capa: !!(window as never as { __mapa?: maplibregl.Map }).__mapa?.getLayer('viento-animado'),
    lienzo: !!document.querySelector('canvas.viento-animado'),
  }));

  expect(animado.capa).toBe(true);
  expect(animado.lienzo).toBe(true);
});

test('el lienzo de partículas no bloquea el mapa', async ({ page }) => {
  // Sin `pointer-events: none` el lienzo taparía el mapa entero y no se podría
  // pulsar ningún incendio: la capa de contexto inutilizaría el dato principal.
  await abrir(page);
  await page.locator('[data-capa="viento"]').click();
  await page.waitForTimeout(2200);

  const pasa = await page.evaluate(() => {
    const c = document.querySelector('canvas.viento-animado');
    return c ? getComputedStyle(c).pointerEvents : null;
  });

  expect(pasa).toBe('none');
});

// --- guarda contra etiquetas mudas -----------------------------------------

test('ninguna capa usa text-field con un estilo sin glyphs', async ({ page }) => {
  // El fallo que mordió dos veces: MapLibre no dibuja `text-field` sin servidor
  // de fuentes y **no protesta**. La capa se añade, el estilo valida y la
  // etiqueta no aparece. Las dos veces se descubrió mirando una captura.
  await abrir(page);
  await page.waitForTimeout(1500);

  const mudas = await page.evaluate(() => {
    const m = (window as never as { __mapa?: maplibregl.Map }).__mapa;
    const estilo = m?.getStyle();
    if (!estilo || estilo.glyphs) return [];
    return (estilo.layers ?? [])
      .filter((c) => 'layout' in c && (c.layout as Record<string, unknown>)?.['text-field'])
      .map((c) => c.id);
  });

  expect(mudas).toEqual([]);
});

test('un fallo de estilo que MapLibre calla se hace visible', async ({ page }) => {
  // Sin esta prueba, el escucha podría estar roto y el test de arriba seguiría
  // en verde por el motivo equivocado: porque no hay capas malas, no porque
  // sepa detectarlas.
  //
  // El caso: MapLibre **no añade** una capa con `text-field` si el estilo no
  // declara `glyphs`. No lanza excepción, no escribe en consola y la capa
  // simplemente no existe. Solo lo dice por el evento `error`.
  await abrir(page);
  await page.waitForTimeout(1500);

  const resultado = await page.evaluate(() => {
    const w = window as never as { __mapa?: maplibregl.Map; __erroresMapa?: string[] };
    const m = w.__mapa;
    if (!m) return null;

    m.addLayer({
      id: 'prueba-muda',
      type: 'symbol',
      source: 'incidentes',
      layout: { 'text-field': 'esto no se vería' },
    });

    return {
      // MapLibre la rechaza: no llega ni a estar en el estilo.
      seAnadio: !!m.getLayer('prueba-muda'),
      registrados: w.__erroresMapa ?? [],
    };
  });

  expect(resultado?.seAnadio).toBe(false);
  expect(resultado?.registrados.join(' ')).toContain('glyphs');
});

// --- Ficha ampliada: fuente, superficie y evolutivo por incendio ------------

/**
 * Abre la ficha del primer incendio de la lista.
 *
 * Se hace por la tarjeta y no por el mapa porque el punto del mapa depende del
 * encuadre y del agrupamiento, y esta prueba no va de eso.
 */
async function abrirPrimeraFicha(page: import('@playwright/test').Page) {
  await abrir(page);
  await page.locator('.tarjeta').first().click();
  await expect(page.locator('#ficha')).toBeVisible();
}

test('la ficha declara qué sensor vio el incendio', async ({ page }) => {
  // El sensor cambia cómo hay que leer la posición: VIIRS son 375 m y MODIS
  // 1 km. Publicarlo sin enseñarlo dejaría el dato muerto en el GeoJSON.
  await abrirPrimeraFicha(page);

  const ficha = page.locator('#ficha');
  await expect(ficha).toContainText('Fuente');
  // Nunca el identificador crudo del producto de la NASA: eso no lo lee nadie.
  await expect(ficha).not.toContainText('_NRT');
});

test('la superficie estimada nunca se presenta como medida', async ({ page }) => {
  await abrirPrimeraFicha(page);

  const ficha = page.locator('#ficha');
  const texto = (await ficha.textContent()) ?? '';
  if (!texto.includes('Superficie estimada')) test.skip();

  // Sobre la ficha entera y no sobre `.ficha__estimacion`: hay varios avisos de
  // estimación y el localizador en modo estricto no admite varios nodos.
  await expect(ficha).toContainText('no medida');
  // El radio equivalente es la misma estimación en otra unidad, y va junto a
  // ella: separarlo lo convertiría en un dato aparte con aire de medición.
  await expect(ficha).toContainText('radio');
});

test('la ficha muestra la evolución de ese incendio, no la global', async ({ page }) => {
  await abrirPrimeraFicha(page);

  const mini = page.locator('#ficha .mini-evolutivo');
  const global = page.locator('#evolutivo .evolutivo__barra');

  if ((await mini.count()) === 0) {
    // Un incendio solo con parte oficial no tiene serie: la caja no se dibuja
    // en vez de salir vacía, que se leería como fallo de carga.
    test.skip();
  }

  const focosFicha = await mini.locator('.mini-evolutivo__cifra').allTextContents();
  const focosGlobal = await global.locator('.evolutivo__cifra').allTextContents();
  expect(focosFicha.join()).not.toBe(focosGlobal.join());
});

test('la leyenda separa intensidad de confirmación', async ({ page }) => {
  // El borde grueso de "oficial" y los tres colores de intensidad son ejes
  // distintos. En una sola lista el borde se leía como un cuarto nivel.
  await abrir(page);

  const leyenda = page.locator('#leyenda');
  await expect(leyenda.locator('.leyenda__grupo')).toHaveCount(2);
  await expect(leyenda).toContainText('calor detectado');
  await expect(leyenda).toContainText('no la gravedad');
});

test('los avisos de AEMET caen sobre España, no en el Índico', async ({ page }) => {
  // CAP escribe los polígonos en orden lat,lon y GeoJSON los quiere al revés.
  // Sin invertir, el mapa sigue pintando polígonos —solo que en Somalia— así
  // que comprobar "hay datos" no detecta nada. Hay que mirar dónde caen.
  await abrir(page);
  await page.locator('[data-capa="avisos"]').click();
  await page.waitForTimeout(1500);

  const limites = await page.evaluate(() => {
    const mapa = (window as never as { __mapa?: maplibregl.Map }).__mapa;
    const fuente = mapa?.getSource('avisos') as maplibregl.GeoJSONSource | undefined;
    const datos = (fuente as unknown as { _data?: GeoJSON.FeatureCollection })?._data;
    if (!datos?.features?.length) return null;

    const coords = datos.features.flatMap((f) =>
      (f.geometry as GeoJSON.Polygon).coordinates.flat(),
    );
    return {
      oeste: Math.min(...coords.map((c) => c[0])),
      este: Math.max(...coords.map((c) => c[0])),
      sur: Math.min(...coords.map((c) => c[1])),
      norte: Math.max(...coords.map((c) => c[1])),
    };
  });

  expect(limites).not.toBeNull();
  expect(limites!.oeste).toBeGreaterThan(-19);
  expect(limites!.este).toBeLessThan(5);
  expect(limites!.sur).toBeGreaterThan(27);
  expect(limites!.norte).toBeLessThan(44);
});

test('la capa de avisos usa los colores oficiales de AEMET', async ({ page }) => {
  // Reinterpretarlos rompería la correspondencia con los partes meteorológicos,
  // que es justo el valor de publicar la declaración oficial y no una propia.
  await abrir(page);
  await page.locator('[data-capa="avisos"]').click();
  await page.waitForTimeout(1500);

  const expresion = await page.evaluate(() => {
    const mapa = (window as never as { __mapa?: maplibregl.Map }).__mapa;
    return JSON.stringify(mapa?.getPaintProperty('avisos-borde', 'line-color'));
  });

  expect(expresion).toContain('#e67e22');
  expect(expresion).toContain('#c0392b');
});

test('la leyenda de avisos aclara que son de AEMET y sobre el tiempo', async ({ page }) => {
  // Un aviso naranja de calor no es un incendio: es la condición que lo
  // favorece. Sin decirlo, la capa se lee al revés — manchas de color sobre el
  // mapa que parecen zonas quemadas.
  await abrir(page);
  const leyenda = page.locator('#leyenda');

  await expect(leyenda).not.toContainText('Avisos oficiales de AEMET');

  await page.locator('[data-capa="avisos"]').click();
  await page.waitForTimeout(1500);

  await expect(leyenda).toContainText('Avisos oficiales de AEMET');
  await expect(leyenda).toContainText('tiempo previsto');
  await expect(leyenda).toContainText('no de que haya');
});

test('la ficha dice hacia dónde sopla el viento, no solo de dónde viene', async ({ page }) => {
  // Es la pregunta con la que se entra al visor: ¿viene hacia mí? Dar solo el
  // origen obliga a hacer la resta mentalmente y es donde la gente se equivoca.
  await abrir(page, '/?lat=40.25&lon=-6.60&zoom=9');
  await page.locator('.tarjeta').first().click();
  await page.waitForTimeout(600);

  const ficha = page.locator('#ficha');
  await expect(ficha).toContainText('Condiciones en la zona');
  await expect(ficha).toContainText('Sopla hacia el');
  await expect(ficha).toContainText('km/h');
});

test('el contexto no se presenta nunca como previsión', async ({ page }) => {
  // Viento observado e interpolado, no pronosticado. Decir "va a soplar" sería
  // una predicción nuestra sobre una aplicación que mira gente asustada.
  await abrir(page, '/?lat=40.25&lon=-6.60&zoom=9');
  await page.locator('.tarjeta').first().click();
  await page.waitForTimeout(600);

  const ficha = page.locator('#ficha');
  await expect(ficha).toContainText('no una previsión');
  await expect(ficha).toContainText('lo declara');
  // Ningún verbo de certeza sobre el futuro del incendio.
  await expect(ficha).not.toContainText(/avanzará|se propagará|llegará a/);
});

test('la ficha atribuye el aviso a AEMET y a la comarca, no al incendio', async ({ page }) => {
  // El aviso se declara sobre una comarca entera. Decir "hay aviso en esta zona"
  // es exacto; decir "hay aviso en este incendio" no lo sería, y sugeriría que
  // AEMET se ha pronunciado sobre este fuego concreto.
  await abrir(page, '/?lat=40.25&lon=-6.60&zoom=9');
  await page.locator('.tarjeta').first().click();
  await page.waitForTimeout(600);

  const ficha = page.locator('#ficha');
  await expect(ficha).toContainText('Aviso de AEMET');
  await expect(ficha).toContainText('sobre la');
  await expect(ficha).toContainText('comarca');
});

test('sin parte oficial no se afirma que el incendio esté activo', async ({ page }) => {
  // El fallo de más alcance que ha tenido el proyecto: los 79 incendios de
  // producción decían «Activo» en rojo sin que ningún servicio de extinción lo
  // hubiera declarado. Una detección de calor de hace horas no dice que el
  // fuego siga vivo.
  await abrir(page);

  const satelital = page.locator('.tarjeta', {
    has: page.locator('.tarjeta__estado[data-estado="sin-declarar"]'),
  }).first();

  await expect(satelital).toBeVisible();
  await expect(satelital.locator('.tarjeta__estado')).toContainText('Calor detectado hace');
  await expect(satelital.locator('.tarjeta__estado')).not.toContainText('Activo');
});

test('el estado declarado por una fuente oficial sí se muestra como tal', async ({ page }) => {
  await abrir(page);

  const oficial = page.locator('.tarjeta', {
    has: page.locator('.tarjeta__estado[data-estado="activo"]'),
  }).first();

  await expect(oficial).toBeVisible();
  await expect(oficial.locator('.tarjeta__estado')).toHaveText('Activo');
});

test('el ritmo se presenta como ya detectado, no como previsión', async ({ page }) => {
  await abrir(page, '/?lat=40.25&lon=-6.60&zoom=9');
  await page.locator('.tarjeta').first().click();
  await page.waitForTimeout(600);

  const ficha = page.locator('#ficha');
  await expect(ficha).toContainText('Focos nuevos');
  await expect(ficha).toContainText('ya detectadas');
  await expect(ficha).not.toContainText(/crecerá hasta|alcanzará|se prevé/);
});

test('los focos de confianza baja no se muestran por defecto', async ({ page }) => {
  // Son quemas agrícolas y reflejos en su mayoría: enseñarlos por defecto
  // llenaría el mapa de puntos que el propio satélite no se cree. Quedan a un
  // clic, que es lo que además los hace medibles.
  await abrir(page, '/?lat=39.10&lon=-2.40&zoom=10');
  await page.waitForTimeout(1500);

  const porDefecto = page.locator('[data-grupo="confianza"] [aria-checked="true"]');
  await expect(porDefecto).toHaveText('Fiables');

  const visibles = async () =>
    await page.evaluate(() => {
      const mapa = (window as never as { __mapa?: maplibregl.Map }).__mapa;
      return mapa?.queryRenderedFeatures({ layers: ['hotspots-punto'] })
        .filter((f) => f.properties?.confianza_baja === true).length ?? -1;
    });

  expect(await visibles()).toBe(0);

  await page.locator('[data-grupo="confianza"] [data-valor="todas"]').click();
  await page.waitForTimeout(900);

  expect(await visibles()).toBeGreaterThan(0);
});

test('el control de confianza explica qué añade antes de pulsarlo', async ({ page }) => {
  await abrir(page);

  const nota = page.locator('#nota-confianza');
  await expect(nota).toBeVisible();
  await expect(nota).toContainText('poco fiables');
  await expect(nota).toContainText('quemas agrícolas');
});

test('la capa de focos nace con el filtro de período aplicado', async ({ page }) => {
  // La capa se monta de forma asíncrona y `aplicarFiltros` corría antes, así que
  // nacía sin filtro. FIRMS se pide con 3 días de margen: en producción 579 de
  // 1.182 focos tenían más de 24 h y se pintaban con el control en «1 día».
  await abrir(page, '/?lat=40.25&lon=-6.60&zoom=10');
  await page.waitForTimeout(1800);

  const filtro = await page.evaluate(() => {
    const mapa = (window as never as { __mapa?: maplibregl.Map }).__mapa;
    return JSON.stringify(mapa?.getFilter('hotspots-punto') ?? null);
  });

  expect(filtro).not.toBe('null');
  expect(filtro).toContain('acq_dt');
  expect(filtro).toContain('confidence_pct');
});

test('la ficha dice a qué distancia está el pueblo más cercano', async ({ page }) => {
  // Es la única línea de la aplicación que responde literalmente a la pregunta
  // con la que se entra: ¿arde algo cerca de mi casa? Y se mide contra los
  // núcleos del IGN, no contra el centroide del término municipal, que está a
  // 3,3 km del pueblo de media y hasta a 23,6 km en el municipio más grande.
  await abrir(page, '/?lat=40.25&lon=-6.60&zoom=9');
  await page.locator('.tarjeta').first().click();
  await page.waitForTimeout(600);

  const ficha = page.locator('#ficha');
  await expect(ficha).toContainText('Núcleo habitado más cercano');
  await expect(ficha).toContainText('km');
  await expect(ficha).toContainText('no a la primera casa');
});

test('una fuente que responde pero dejó de publicar sale como rancia', async ({ page }) => {
  // A7 · el 31-07-2026 FIRMS dejó de servir VIIRS y el panel decía «correcto ·
  // 883 registros · hace 15 s», porque la descarga del archivo de tres días
  // seguía funcionando. Una fuente muerta parecía sana.
  await page.route('**/live/sources.json', (ruta) =>
    ruta.fulfill({
      json: {
        generated_at: new Date().toISOString(),
        sources: [
          {
            id: 'firms_viirs', name: 'NASA FIRMS · VIIRS', region: 'España',
            kind: 'satelite', critical: true, status: 'stale',
            last_success_at: new Date().toISOString(), age_seconds: 15,
            ttl_seconds: 600, records: 883, precision_m: 375, error: null,
            consecutive_failures: 0, attribution: 'NASA FIRMS',
            quota_remaining: 4996, quota_limit: 5000,
            data_age_seconds: 72000, max_data_age_seconds: 43200,
            stale_reason: 'responde, pero sin datos nuevos desde hace 20 h',
          },
        ],
      },
    }),
  );
  await abrir(page);

  const fuente = page.locator('.fuente', { hasText: 'VIIRS' }).first();

  await expect(fuente).toContainText('sin datos nuevos desde hace 20 h');
  // Lo que NO debe decir: que todo va bien porque bajamos el fichero.
  await expect(fuente).not.toContainText('883 registros · hace 15 s');
});

test('la cabecera explica qué significa cada latencia', async ({ page }) => {
  // Confundir las dos es el error que este visor existe para no cometer, y hasta
  // quien lo construyó dudó de cuál era cuál.
  await abrir(page);

  const dato = page.locator('.latencia__bloque', { hasText: 'Datos satelitales' });
  const ejecucion = page.locator('.latencia__bloque', { hasText: 'Actualización' });

  await expect(dato).toHaveAttribute('title', /satélite vio/);
  await expect(ejecucion).toHaveAttribute('title', /pipeline/);
});

test('el intervalo declarado coincide con el del cron', async ({ page }) => {
  // Decía «cada 10 min» desde una versión anterior; el cron corre cada 30.
  await abrir(page);

  await expect(page.locator('.latencia__pie').last()).toHaveText('refresco cada 30 min');
});

test('la antigüedad se desglosa por sensor, no solo la peor', async ({ page }) => {
  // Con un único número, «19 h 41 min» se lee como que todo está viejo. El
  // 31-07-2026 esa era la cifra y MODIS tenía 5,6 h: lo parado era VIIRS.
  //
  // Se comprueba que hay **más de un sensor** y su antigüedad, no unos nombres
  // concretos: qué sensores haya depende del día y de qué publique cada uno.
  await abrir(page);

  const pie = await page.locator('#latencia-dato-pie').innerText();

  expect(pie.split('·').length).toBeGreaterThan(1);
  expect(pie).toMatch(/\d+\s*(min|h)/);
});

test('la ficha cita la dirección con las palabras de la fuente', async ({ page }) => {
  // «CV-223 Km4 Eslida > Aín, a mano derecha» lo escribe el operador del 112 y
  // sitúa el fuego respecto a una carretera, que es como la gente localiza las
  // cosas. Va entrecomillado porque no son nuestras palabras.
  await abrir(page, '/?lat=39.90&lon=-0.38&zoom=11');
  await page.locator('.tarjeta').first().click();
  await page.waitForTimeout(600);

  const ficha = page.locator('#ficha');
  await expect(ficha).toContainText('Dónde');
  await expect(ficha.locator('.ficha__nota--cita')).toBeVisible();
  // Y nunca la palabra que salía cuando el campo venía vacío.
  await expect(ficha).not.toContainText(/\bnan\b/);
});

test('la ficha dice sobre qué terreno cae el incendio', async ({ page }) => {
  // Separa el incendio forestal de la quema agrícola: un «incendio» sobre
  // cultivo en julio es casi siempre rastrojo. No se filtra por esto —una quema
  // que se descontrola es cómo empiezan muchos incendios forestales— se etiqueta.
  await abrir(page, '/?lat=40.25&lon=-6.60&zoom=9');
  await page.locator('.tarjeta').first().click();
  await page.waitForTimeout(600);

  const ficha = page.locator('#ficha');
  await expect(ficha).toContainText('Terreno');
  await expect(ficha).toContainText(/Bosque|Matorral|Cultivo|Pastizal|Superficie/);
});

/**
 * Corta las teselas de CORINE con un PNG mínimo.
 *
 * Sin esto, estas dos pruebas salen al servidor de la Agencia Europea de Medio
 * Ambiente, y la suite tiene que correr sin red: en CI eso es una dependencia
 * externa que puede estar lenta o caída, y aquí lo que se comprueba es **el
 * orden de las capas y la leyenda**, no que la EEA responda.
 */
async function sinTeselasReales(page: import('@playwright/test').Page) {
  const pngVacio = Buffer.from(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
    'base64',
  );
  await page.route('**/discomap.eea.europa.eu/**', (ruta) =>
    ruta.fulfill({ contentType: 'image/png', body: pngVacio }),
  );
}

test('la capa de terreno se monta por debajo de los focos', async ({ page }) => {
  // Si se añadiera encima taparía los incendios, que son el objeto del visor.
  await sinTeselasReales(page);
  await abrir(page, '/?lat=40.25&lon=-6.60&zoom=9');
  await page.locator('[data-capa="suelo"]').click();
  await page.waitForTimeout(1800);

  const orden = await page.evaluate(() => {
    const mapa = (window as never as { __mapa?: maplibregl.Map }).__mapa;
    return mapa?.getStyle().layers.map((l) => l.id) ?? [];
  });

  expect(orden).toContain('suelo-raster');
  expect(orden.indexOf('suelo-raster')).toBeLessThan(orden.indexOf('hotspots-punto'));
});

test('la leyenda del terreno explica los colores y su limitación', async ({ page }) => {
  // CORINE tiene 44 tonos y se conservan tal cual porque el servidor los pinta.
  // Agrupar por familia es lo que hace legible el mapa sin tocar el ráster.
  await sinTeselasReales(page);
  await abrir(page);
  await page.locator('[data-capa="suelo"]').click();
  await page.waitForTimeout(1500);

  const leyenda = page.locator('#leyenda');

  await expect(leyenda).toContainText('Tipo de terreno');
  await expect(leyenda).toContainText('monte, matorral');
  await expect(leyenda).toContainText('cultivos');
  // Y la advertencia: es cartografía de 2018, no el estado de hoy.
  await expect(leyenda).toContainText('CORINE 2018');
  await expect(leyenda).toContainText('no del');
  // El negro parece un fallo de carga y no lo es: es la clase 334, terreno que
  // ya había ardido. Sin explicarlo, el usuario lo lee como un bug.
  await expect(leyenda).toContainText('ya quemada');
  await expect(leyenda).toContainText('ya había ardido');
  // Y por qué se emborrona al acercarse, que si no parece que falle.
  await expect(leyenda).toContainText('25 ha');
});

test('el terreno no desaparece al acercar el mapa', async ({ page }) => {
  // CORINE deja de servir datos a partir de zoom 12: devuelve una tesela
  // transparente de 886 bytes. Con `maxzoom` mal puesto, MapLibre pedía esas y
  // la capa se esfumaba justo cuando el usuario se acerca a mirar el detalle.
  await abrir(page, '/?lat=40.25&lon=-6.60&zoom=9');
  await page.locator('[data-capa="suelo"]').click();
  await page.waitForTimeout(2000);

  const maxzoom = await page.evaluate(() => {
    const mapa = (window as never as { __mapa?: maplibregl.Map }).__mapa;
    const fuente = mapa?.getStyle().sources['suelo'] as { maxzoom?: number };
    return fuente?.maxzoom;
  });

  // Por encima de 11 se piden teselas que el servicio devuelve vacías.
  expect(maxzoom).toBeLessThanOrEqual(11);

  // Y la capa sigue montada tras acercarse a escala de calle.
  await page.evaluate(() => {
    (window as never as { __mapa?: maplibregl.Map }).__mapa?.setZoom(16);
  });
  await page.waitForTimeout(1200);

  const sigue = await page.evaluate(
    () => !!(window as never as { __mapa?: maplibregl.Map }).__mapa?.getLayer('suelo-raster'),
  );
  expect(sigue).toBe(true);
});
