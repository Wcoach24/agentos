#!/usr/bin/env bash
# AgentOS — reintento limpio de la misión de prueba (sin comentarios inline para
# evitar el problema de globbing de zsh al pegar). Uso:  bash run.sh
set -uo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "ERROR: no hay .venv aquí. Crea el entorno primero:"
  echo "  python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi
source .venv/bin/activate

if ! command -v claude >/dev/null 2>&1; then
  echo "AVISO: el CLI 'claude' no está en el PATH."
  echo "  El SDK necesita el login OAuth de tu plan Max. Hazlo una vez:"
  echo "    npm install -g @anthropic-ai/claude-code"
  echo "    claude            # login en el navegador, elige tu plan"
  echo "    claude /status    # debe decir plan, no API key"
  echo "  (si no tienes npm:  brew install node)"
  echo "Abortando hasta que el login esté hecho."
  exit 1
fi

# Estado limpio para arrancar de cero (no resume el thread roto de la corrida fallida)
rm -rf state/checkpoints.sqlite state/_smoke missions/active/* 2>/dev/null || true

# Cargar secretos del .env ya relleno
set -a; source .env; set +a

# Regla de oro: forzar el plan, nunca API key
unset ANTHROPIC_API_KEY 2>/dev/null || true

echo "== smoke =="
python smoke_test.py || { echo "smoke FAIL"; exit 1; }
echo "== misión =="
python -m orchestrator.runner missions/inbox/2026-06-16-geo-es-dossier.yaml
