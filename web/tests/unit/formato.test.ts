import { describe, expect, it } from 'vitest';

import { edadAhora, UMBRAL_PIPELINE_PARADO_S } from '../../src/formato';

const PUBLICADO = '2026-10-03T23:40:00Z';
const ahora = (iso: string) => Date.parse(iso);

describe('edadAhora', () => {
  it('suma al dato el tiempo transcurrido desde que se publicó', () => {
    // El caso real: dato de 37 h al publicar, cron parado 37 días.
    const edad = edadAhora(37 * 3600, PUBLICADO, ahora('2026-11-09T23:40:00Z'));
    expect(edad).toBe(37 * 3600 + 37 * 86400);
  });

  it('recién publicado, la edad es la del fichero', () => {
    expect(edadAhora(600, PUBLICADO, ahora(PUBLICADO))).toBe(600);
  });

  it('sin edad no inventa una', () => {
    expect(edadAhora(null, PUBLICADO)).toBeNull();
    expect(edadAhora(undefined, PUBLICADO)).toBeNull();
  });

  it('con una marca ilegible devuelve la edad del fichero, no cero', () => {
    expect(edadAhora(900, 'no-es-fecha')).toBe(900);
    expect(edadAhora(900, null)).toBe(900);
  });

  it('un reloj del cliente adelantado al servidor no resta edad', () => {
    expect(edadAhora(900, PUBLICADO, ahora('2026-10-03T23:00:00Z'))).toBe(900);
  });
});

describe('UMBRAL_PIPELINE_PARADO_S', () => {
  it('tolera el retraso habitual de los cron de GitHub (≤ 1 h)', () => {
    expect(UMBRAL_PIPELINE_PARADO_S).toBeGreaterThan(90 * 60);
  });
});
