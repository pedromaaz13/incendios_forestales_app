import { defineConfig } from 'vite';

export default defineConfig({
  build: {
    target: 'es2022',
    // RNF-02: presupuesto de 900 KB para la carga inicial. El aviso salta antes
    // de que nadie lo note en producción.
    chunkSizeWarningLimit: 900,
  },
});
