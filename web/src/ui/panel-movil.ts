/**
 * Cajón del panel en móvil.
 *
 * Por qué existe: por debajo de 860 px el panel es un cajón que sale de la
 * izquierda, y el CSS ya contemplaba `data-abierto`. Lo que faltaba era que
 * alguien lo pusiera. Sin esto, desde un teléfono el visor era **solo el mapa**:
 * ni buscador, ni filtros, ni capas, ni «Mis activos», ni el estado de las
 * fuentes. Medido el 04-08-2026 a 390 px: el panel quedaba en x = −283 y no
 * había ningún control para traerlo.
 *
 * Importa más de lo que parece porque el móvil no es el caso secundario aquí:
 * quien busca «incendio cerca de mi pueblo» lo hace desde el teléfono.
 */

const ANCHO_MOVIL = 860;

export function construirPanelMovil(): void {
  const boton = document.getElementById('panel-boton');
  const panel = document.getElementById('panel-izq');
  const velo = document.getElementById('panel-velo');
  if (!boton || !panel || !velo) return;

  const abrir = (abierto: boolean) => {
    panel.dataset.abierto = String(abierto);
    boton.setAttribute('aria-expanded', String(abierto));
    velo.hidden = !abierto;
  };

  boton.addEventListener('click', () => {
    abrir(panel.dataset.abierto !== 'true');
  });

  // Tocar fuera cierra: en una pantalla estrecha el cajón tapa el mapa, y
  // obligar a buscar el botón otra vez para volver a ver dónde arde es
  // exactamente la fricción que no puede tener esto.
  velo.addEventListener('click', () => abrir(false));

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && panel.dataset.abierto === 'true') {
      abrir(false);
      (boton as HTMLButtonElement).focus();
    }
  });

  // Al elegir algo del panel se cierra solo: casi todo lo que hay dentro
  // —buscar un pueblo, activar una capa, aplicar un cruce— tiene su efecto en
  // el mapa, y dejarlo tapado obligaría a cerrarlo a mano cada vez.
  panel.addEventListener('click', (e) => {
    if (window.innerWidth > ANCHO_MOVIL) return;
    const objetivo = e.target as HTMLElement;
    if (objetivo.closest('.buscador__opcion, .tarjeta, .cruce, [data-capa], .activos__punto')) {
      abrir(false);
    }
  });

  // Al ensanchar la ventana el panel vuelve a ser fijo y el velo sobraría.
  window.addEventListener('resize', () => {
    if (window.innerWidth > ANCHO_MOVIL) abrir(false);
  });
}
