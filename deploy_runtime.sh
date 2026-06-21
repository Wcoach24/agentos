#!/usr/bin/env bash
# deploy_runtime.sh — Reubica el runtime del daemon FUERA de ~/Desktop.
# macOS bloquea a los procesos launchd leer ~/Desktop / ~/Documents / ~/Downloads (TCC),
# así que el daemon no puede leer su .env ni su código si vive en Desktop. Esto copia todo
# a ~/agentos (no protegido), crea el venv allí, ajusta el plist y recarga el daemon.
#
# Úsalo: cada vez que cambie el código en ~/Desktop/os/agent-os, re-ejecútalo para sincronizar.
set -uo pipefail
SRC="$HOME/Desktop/os/agent-os"
DST="$HOME/agentos"

echo "Reubicando runtime: $SRC  ->  $DST  (fuera de ~/Desktop por TCC de macOS)"
[ -d "$SRC" ] || { echo "No encuentro $SRC"; exit 1; }
mkdir -p "$DST" "$DST/state"

# 1) copiar código + .env (sin el venv NI state/: state es runtime puro —checkpoints,
#    logs, tg_offset, flags como _FROZEN—; se preserva en el destino entre re-syncs y así
#    nada de basura local del Desktop, p.ej. un _FROZEN de prueba, congela el daemon).
rsync -a --exclude '.venv' --exclude 'state/' "$SRC/" "$DST/"

# 2) venv en el destino (una sola vez; reinstala deps si falta)
if [ ! -x "$DST/.venv/bin/python" ]; then
  echo "Creando venv en $DST/.venv ..."
  python3 -m venv "$DST/.venv"
  "$DST/.venv/bin/pip" install -q --disable-pip-version-check -r "$DST/requirements.txt"
fi

# 3) plist con las rutas del destino + recargar el daemon
PLIST="$DST/install/com.alvaro.agentos.watcher.plist"
sed -i '' "s#/Users/[^/]*/Desktop/os/agent-os#$DST#g" "$PLIST"
DEST="$HOME/Library/LaunchAgents/com.alvaro.agentos.watcher.plist"
cp "$PLIST" "$DEST"
launchctl unload "$DEST" 2>/dev/null || true
launchctl load "$DEST"
launchctl start com.alvaro.agentos.watcher 2>/dev/null || true

echo "✅ Daemon recargado desde $DST (sin bloqueo TCC)."
echo "   Logs:  tail -f $DST/state/watcher.out.log   (errores: state/watcher.err.log)"
echo "   Deberías recibir '👀 AgentOS arrancado' en Telegram en unos segundos."
