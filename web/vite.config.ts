import { defineConfig } from 'vite';

export default defineConfig({
  // GitHub Pages sirve bajo /nombre-del-repo/, no bajo la raíz. Cloudflare
  // Pages y el desarrollo local sí usan la raíz, así que la base se inyecta
  // desde el workflow en lugar de fijarla aquí.
  base: process.env.BASE_PATH || '/',
  build: {
    target: 'es2022',
    // RNF-02: presupuesto de 900 KB para la carga inicial. El aviso salta antes
    // de que nadie lo note en producción.
    chunkSizeWarningLimit: 900,
  },
});
