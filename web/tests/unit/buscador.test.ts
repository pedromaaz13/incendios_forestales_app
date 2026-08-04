import { describe, expect, it } from 'vitest';

import { buscar, normalizar, type Nucleo } from '../../src/ui/buscador';

const n = (nombre: string, habitantes = 0): Nucleo => ({ nombre, lat: 40, lon: -3, habitantes });

// El índice real llega ordenado por población descendente y `buscar` cuenta con
// ello para poder cortar antes de recorrer los 37.497.
const INDICE: Nucleo[] = [
  n('Madrid', 3_422_416),
  n('Ávila', 58_000),
  n('Villanueva de la Serena', 25_800),
  n('Villanueva del Pardillo', 18_000),
  n('Peñalba de Ávila', 250),
  n('Navaluenga', 2_100),
];

describe('normalizar', () => {
  it('quita acentos y baja a minúsculas', () => {
    // Quien está asustado escribe «avila» desde el móvil, no «Ávila».
    expect(normalizar('Ávila')).toBe('avila');
    expect(normalizar('  Cangas de Onís ')).toBe('cangas de onis');
  });

  it('también pliega la eñe, a propósito', () => {
    // En castellano la ñ es una letra propia, no una n con virgulilla, así que
    // esto es una decisión y no un descuido: plegarla hace que «penalba»
    // encuentre «Peñalba», y quien busca su pueblo desde un teclado ajeno o con
    // prisa escribe sin ñ más a menudo de lo que la escribe bien. El coste
    // —confundir «peña» con «pena»— no tiene consecuencia en un buscador de
    // topónimos.
    expect(normalizar('Peñalba')).toBe('penalba');
  });

  it('encuentra un topónimo con eñe escrito sin ella', () => {
    expect(buscar(INDICE, 'penalba').map((x) => x.nombre)).toContain('Peñalba de Ávila');
  });
});

describe('buscar', () => {
  it('encuentra ignorando acentos', () => {
    expect(buscar(INDICE, 'avila').map((x) => x.nombre)).toContain('Ávila');
  });

  it('antepone lo que empieza por lo tecleado', () => {
    // Buscando «Ávila» se quiere Ávila, no «Peñalba de Ávila».
    expect(buscar(INDICE, 'avila')[0].nombre).toBe('Ávila');
  });

  it('a igualdad de prefijo, primero el más poblado', () => {
    const nombres = buscar(INDICE, 'villanueva').map((x) => x.nombre);
    expect(nombres[0]).toBe('Villanueva de la Serena');
  });

  it('no busca con menos de dos caracteres', () => {
    // Con una letra los resultados no informan y el coste es recorrer el índice
    // entero en cada pulsación.
    expect(buscar(INDICE, 'a')).toEqual([]);
    expect(buscar(INDICE, '')).toEqual([]);
  });

  it('respeta el máximo pedido', () => {
    expect(buscar(INDICE, 'a', 3)).toHaveLength(0);
    expect(buscar(INDICE, 'vi', 1)).toHaveLength(1);
  });

  it('devuelve vacío si no hay nada que coincida', () => {
    expect(buscar(INDICE, 'zzzz')).toEqual([]);
  });

  it('encuentra también por el medio del nombre', () => {
    expect(buscar(INDICE, 'serena').map((x) => x.nombre)).toContain('Villanueva de la Serena');
  });
});
