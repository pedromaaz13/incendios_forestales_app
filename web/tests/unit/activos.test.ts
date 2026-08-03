import { describe, expect, it } from 'vitest';

import {
  ARCO_SOTAVENTO_GRADOS,
  calcularExposicion,
  diferenciaAngular,
  distanciaKm,
  ErrorDeFichero,
  leerCSV,
  leerGeoJSON,
  type RasgoIncidente,
  rumboGrados,
} from '../../src/ui/activos';

/**
 * La aritmética que decide si un activo está expuesto.
 *
 * Es el código de este proyecto con más probabilidad de fallar **sin dar
 * error**: un rumbo mal calculado no revienta nada, dice «a sotavento» cuando
 * el viento sopla al revés. Por eso las distancias esperadas salen de
 * referencias independientes —distancias conocidas entre ciudades— y no de
 * ejecutar la propia función y copiar el resultado.
 */

function incendio(lat: number, lon: number, vientoHaciaDeg: number | null): RasgoIncidente {
  return {
    type: 'Feature',
    geometry: { type: 'Point', coordinates: [lon, lat] },
    properties: { id: 'x', viento_hacia_deg: vientoHaciaDeg },
  } as unknown as RasgoIncidente;
}

describe('distanciaKm', () => {
  it('coincide con distancias conocidas', () => {
    // Madrid–Barcelona son ~505 km en línea recta; Madrid–Sevilla ~390 km.
    expect(distanciaKm(40.4169, -3.7038, 41.4036, 2.1744)).toBeCloseTo(505, -1);
    expect(distanciaKm(40.4169, -3.7038, 37.3861, -5.9926)).toBeCloseTo(391, -1);
  });

  it('es cero sobre el mismo punto y simétrica', () => {
    expect(distanciaKm(40, -3, 40, -3)).toBe(0);
    expect(distanciaKm(40, -3, 41, -2)).toBeCloseTo(distanciaKm(41, -2, 40, -3), 9);
  });
});

describe('rumboGrados', () => {
  it('da los cuatro puntos cardinales', () => {
    expect(rumboGrados(40, -3, 41, -3)).toBeCloseTo(0, 0); // norte
    expect(rumboGrados(40, -3, 40, -2)).toBeCloseTo(90, 0); // este
    expect(rumboGrados(40, -3, 39, -3)).toBeCloseTo(180, 0); // sur
    expect(rumboGrados(40, -3, 40, -4)).toBeCloseTo(270, 0); // oeste
  });

  it('siempre devuelve entre 0 y 360', () => {
    for (const [aLat, aLon, bLat, bLon] of [
      [40, -3, 41, -4],
      [43, -8, 36, 2],
      [28, -16, 40, -3],
    ]) {
      const r = rumboGrados(aLat, aLon, bLat, bLon);
      expect(r).toBeGreaterThanOrEqual(0);
      expect(r).toBeLessThan(360);
    }
  });
});

describe('diferenciaAngular', () => {
  it('cruza el norte por el camino corto', () => {
    // 350° y 10° están a 20°, no a 340°. Sin esto, un incendio con viento del
    // norte nunca marcaría sotavento hacia el norte.
    expect(diferenciaAngular(350, 10)).toBe(20);
    expect(diferenciaAngular(10, 350)).toBe(20);
  });

  it('nunca pasa de 180', () => {
    expect(diferenciaAngular(0, 181)).toBe(179);
    expect(diferenciaAngular(0, 180)).toBe(180);
  });
});

describe('calcularExposicion · sotavento', () => {
  const activoAlNorte = [{ nombre: 'Nave', lat: 41, lon: -3 }];

  it('marca sotavento cuando el viento sopla hacia el activo', () => {
    // Incendio al sur del activo, viento hacia el norte (0°): le llega.
    const [e] = calcularExposicion(activoAlNorte, [incendio(40, -3, 0)]);
    expect(e.aSotavento).toBe(true);
  });

  it('no marca sotavento cuando el viento sopla al contrario', () => {
    const [e] = calcularExposicion(activoAlNorte, [incendio(40, -3, 180)]);
    expect(e.aSotavento).toBe(false);
  });

  it('respeta el borde del cono', () => {
    const justoDentro = ARCO_SOTAVENTO_GRADOS - 1;
    const justoFuera = ARCO_SOTAVENTO_GRADOS + 1;
    expect(calcularExposicion(activoAlNorte, [incendio(40, -3, justoDentro)])[0].aSotavento).toBe(
      true,
    );
    expect(calcularExposicion(activoAlNorte, [incendio(40, -3, justoFuera)])[0].aSotavento).toBe(
      false,
    );
  });

  it('sin viento publicado deja nulo, no falso', () => {
    // La distinción es el punto entero: «no sabemos hacia dónde sopla» no es
    // «no sopla hacia ti».
    const [e] = calcularExposicion(activoAlNorte, [incendio(40, -3, null)]);
    expect(e.aSotavento).toBeNull();
  });

  it('sin incendios no inventa distancia', () => {
    const [e] = calcularExposicion(activoAlNorte, []);
    expect(e.distanciaKm).toBeNull();
    expect(e.incendio).toBeNull();
  });
});

describe('calcularExposicion · orden', () => {
  it('pone primero lo más expuesto y deja lo desconocido al final', () => {
    const activos = [
      { nombre: 'Lejos', lat: 43, lon: -3 },
      { nombre: 'Cerca a sotavento', lat: 40.1, lon: -3 },
    ];
    const orden = calcularExposicion(activos, [incendio(40, -3, 0)]).map((e) => e.activo.nombre);
    expect(orden[0]).toBe('Cerca a sotavento');
  });

  it('elige el incendio más cercano, no el primero de la lista', () => {
    const [e] = calcularExposicion([{ nombre: 'Nave', lat: 40, lon: -3 }], [
      incendio(43, -3, null),
      incendio(40.05, -3, null),
    ]);
    expect(e.distanciaKm).toBeLessThan(10);
  });
});

describe('leerCSV', () => {
  it('lee el formato básico', () => {
    const a = leerCSV('nombre,lat,lon\nNave,40.5,-3.7');
    expect(a).toEqual([{ nombre: 'Nave', lat: 40.5, lon: -3.7 }]);
  });

  it('acepta el CSV que exporta Excel en español', () => {
    // Punto y coma como separador y coma decimal: es lo que sale de un Excel
    // con configuración regional española, y obligar a convertirlo antes de
    // poder mirar el mapa es la fricción que hace que nadie use la herramienta.
    const a = leerCSV('nombre;lat;lon\nNave;40,5;-3,7');
    expect(a).toEqual([{ nombre: 'Nave', lat: 40.5, lon: -3.7 }]);
  });

  it('acepta nombres de columna alternativos', () => {
    expect(leerCSV('name,latitud,longitud\nNave,40.5,-3.7')[0].lat).toBe(40.5);
    expect(leerCSV('id,y,x\nNave,40.5,-3.7')[0].lon).toBe(-3.7);
  });

  it('pone nombre por defecto si no hay columna', () => {
    expect(leerCSV('lat,lon\n40.5,-3.7')[0].nombre).toBe('Punto 1');
  });

  it('se queja si faltan las coordenadas', () => {
    expect(() => leerCSV('nombre,notas\nNave,nada')).toThrow(ErrorDeFichero);
  });

  it('detecta un lote con latitud y longitud cambiadas', () => {
    // El error más común y el más difícil de ver: los puntos aparecen en el
    // mapa, pero en Somalia.
    expect(() => leerCSV('nombre,lat,lon\nA,-3.70,40.50\nB,-5.99,37.39')).toThrow(/invertidas/);
  });

  it('salta las filas sin coordenada válida en vez de tumbar el fichero', () => {
    const a = leerCSV('nombre,lat,lon\nBuena,40.5,-3.7\nMala,,\nOtra,41,-3');
    expect(a).toHaveLength(2);
  });
});

describe('leerGeoJSON', () => {
  it('lee puntos y su nombre', () => {
    const a = leerGeoJSON(
      JSON.stringify({
        type: 'FeatureCollection',
        features: [
          {
            type: 'Feature',
            geometry: { type: 'Point', coordinates: [-3.7, 40.5] },
            properties: { nombre: 'Nave' },
          },
        ],
      }),
    );
    expect(a).toEqual([{ nombre: 'Nave', lat: 40.5, lon: -3.7 }]);
  });

  it('ignora geometrías que no son puntos', () => {
    const conLinea = {
      type: 'FeatureCollection',
      features: [
        { type: 'Feature', geometry: { type: 'LineString', coordinates: [] }, properties: {} },
        {
          type: 'Feature',
          geometry: { type: 'Point', coordinates: [-3.7, 40.5] },
          properties: {},
        },
      ],
    };
    expect(leerGeoJSON(JSON.stringify(conLinea))).toHaveLength(1);
  });

  it('se queja de un JSON roto en vez de reventar', () => {
    expect(() => leerGeoJSON('{no es json')).toThrow(ErrorDeFichero);
  });
});
