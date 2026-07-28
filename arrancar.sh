#!/usr/bin/env bash
#
# Arranca el visor con datos REALES de NASA FIRMS en tu ordenador.
#
# No necesita Cloudflare, ni R2, ni GitHub Actions, ni servidor. Solo la clave
# gratuita de FIRMS. Es la forma más corta de ver el MVP funcionando.
#
#   export FIRMS_MAP_KEY=tu-clave
#   ./arrancar.sh
#
# Sin clave arranca igual, con datos de demostración y un aviso permanente.

set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$RAIZ"

azul() { printf '\033[36m%s\033[0m\n' "$1"; }
verde() { printf '\033[32m%s\033[0m\n' "$1"; }
ambar() { printf '\033[33m%s\033[0m\n' "$1"; }
rojo() { printf '\033[31m%s\033[0m\n' "$1"; }

azul "== Incendios forestales · arranque local =============================="

# --- la clave -----------------------------------------------------------------
#
# Se lee de `.env` si existe. Ese fichero está en .gitignore, así que la clave
# no puede acabar en un commit por descuido: es el único sitio donde debe vivir
# en local. Nunca en el código, nunca en un chat.

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

# --- comprobaciones previas -------------------------------------------------

command -v python3 >/dev/null || { rojo "Falta python3."; exit 1; }
command -v node >/dev/null || { rojo "Falta node. Instala Node 20 o superior."; exit 1; }

if [ ! -d .venv ]; then
  azul "Creando entorno virtual de Python..."
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

azul "Instalando dependencias de Python..."
pip install -q -r requirements.txt

if [ ! -d web/node_modules ]; then
  azul "Instalando dependencias del frontend..."
  (cd web && npm install --no-fund --no-audit)
fi

# --- datos ------------------------------------------------------------------

export PYTHONPATH=src

if [ -n "${FIRMS_MAP_KEY:-}" ]; then
  azul "Comprobando la clave de FIRMS..."
  if python scripts/descubrir_fuentes.py --firms | grep -q "Clave válida"; then
    verde "Clave correcta. Descargando incendios reales de las últimas horas..."

    # `--sin-viento` no: el viento de Open-Meteo tampoco necesita clave.
    # Si falla, el pipeline sigue: es contexto, no un bloqueante.
    if python -m incendios.pipeline -v --no-raw; then
      verde "Pipeline completado con datos reales."
      mkdir -p web/public/live
      cp data/out/*.geojson data/out/*.json web/public/live/ 2>/dev/null || true
    else
      rojo "El pipeline abortó. Arriba tienes el motivo."
      ambar "Se arranca con datos de demostración para que puedas ver la interfaz."
      python scripts/build_demo_data.py
    fi
  else
    rojo "La clave de FIRMS no funciona."
    ambar "Arrancando con datos de demostración."
    python scripts/build_demo_data.py
  fi
else
  ambar "FIRMS_MAP_KEY no está definida: se arranca con datos de DEMOSTRACIÓN."
  ambar "Para ver incendios reales:"
  ambar "   1. Pide la clave gratis en https://firms.modaps.eosdis.nasa.gov/api/map_key/"
  ambar "   2. export FIRMS_MAP_KEY=la-clave"
  ambar "   3. vuelve a lanzar ./arrancar.sh"
  python scripts/build_demo_data.py
fi

# --- servidor ---------------------------------------------------------------

echo
verde "== Listo ==============================================================="
verde "   Abre  http://localhost:5173  en el navegador."
verde "   Ctrl+C para parar."
echo

cd web && npm run dev
