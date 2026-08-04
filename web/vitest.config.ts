import { defineConfig } from 'vitest/config';

/**
 * Pruebas unitarias del frontend.
 *
 * Por qué hacen falta teniendo Playwright: la aritmética de este visor —rumbos,
 * distancias, sotavento— es exactamente el código que falla devolviendo **un
 * número plausible y equivocado**. Un rumbo mal calculado no revienta nada:
 * dice «a sotavento» cuando el viento sopla al revés, y nadie lo nota. Probarlo
 * de rebote pinchando la interfaz no sirve, porque a través de la interfaz solo
 * se ve la etiqueta final, no el ángulo que la produjo.
 *
 * Se separan de `tests/e2e/` a propósito: Playwright tiene ahí su `testDir` y
 * mezclarlos haría que cada suite intentara ejecutar la de la otra.
 */
export default defineConfig({
  test: {
    include: ['tests/unit/**/*.test.ts'],
    environment: 'node',
  },
});
