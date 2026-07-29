import { defineConfig, devices } from '@playwright/test';

/**
 * Arnés de capturas y E2E · secciones 8.4 y 9.
 *
 * Todo contra `vite preview` con datos de fixture, nunca contra producción: las
 * pruebas tienen que ser deterministas y una captura que dependa de qué esté
 * ardiendo hoy no sirve para comparar entre PRs.
 */
export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? [['github'], ['list']] : [['list']],

  use: {
    baseURL: 'http://localhost:4173',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    // Chromium de sistema: el entorno lo trae preinstalado y descargarlo otra
    // vez en CI es un minuto por ejecución que no aporta nada.
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM
      ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM }
      : {},
  },

  projects: [
    { name: 'escritorio', use: { ...devices['Desktop Chrome'], viewport: { width: 1680, height: 1050 } } },
  ],

  webServer: {
    // Se compila **aquí** y no antes: `vite preview` sirve `dist/`, y `dist/`
    // solo tiene los datos si `public/live/` ya existía en el momento del
    // build. En CI el orden era compilar y luego generar los datos, así que
    // `dist/live/` quedaba vacío, cada `fetch` de JSON recibía el `index.html`
    // del fallback y las pruebas fallaban con "Unexpected token '<'". En local
    // no se veía porque `public/live/` conserva los datos de la vez anterior.
    // Compilando dentro del arranque del servidor, los datos ya están puestos
    // llegue como llegue.
    command: 'npx vite build && npm run preview -- --port 4173 --strictPort',
    url: 'http://localhost:4173',
    reuseExistingServer: true,
    // El build tarda unos segundos más que arrancar el preview a secas.
    timeout: 120_000,
  },
});
