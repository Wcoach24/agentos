#!/usr/bin/env bash
# confirm.sh — CONFIRMA el end-to-end REAL con el SDK del plan Max.
# Misión mínima, barata (~$0.x), sin web, sin gates: solo prueba que el loop cierra
# de verdad (plan -> bookkeep -> verify[3 checks de máquina] -> cierre + aviso Telegram).
set -uo pipefail
cd "$(dirname "$0")"

[ -d .venv ] || { echo "Falta .venv. Crea el entorno primero:"; echo "  python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"; exit 1; }
source .venv/bin/activate
if ! command -v claude >/dev/null 2>&1; then
  echo "Falta 'claude' en el PATH (login del plan Max). Mira run.sh para el setup."; exit 1
fi

MID="2026-06-17-confirm-e2e"
rm -rf state/checkpoints.sqlite "missions/active/$MID" "missions/active/$MID.yaml" "missions/done/$MID" 2>/dev/null || true
set -a; source .env 2>/dev/null; set +a
unset ANTHROPIC_API_KEY 2>/dev/null || true

echo "== smoke (offline, sin coste) =="
python smoke_test.py >/dev/null && echo "smoke OK" || { echo "smoke FAIL"; exit 1; }

echo "== misión de confirmación (SDK real) =="
python -m orchestrator.runner "missions/inbox/$MID.yaml"

echo "--------"
R="missions/done/$MID/_RESULT.json"
if [ -f "$R" ] && grep -q '"ok": true' "$R"; then
  echo "✅ END-TO-END REAL OK — el sistema cierra solo. (Te ha llegado un aviso 'COMPLETADA' a Telegram.)"
else
  echo "❌ No cerró limpio. Pega TODA la salida de arriba y lo afino."
fi
