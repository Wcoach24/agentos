
#!/usr/bin/env bash
# Arranca (o reinstala) el daemon del watcher en launchd. UN solo comando.
set -uo pipefail
cd "$(dirname "$0")"
SRC="install/com.alvaro.agentos.watcher.plist"
DEST="$HOME/Library/LaunchAgents/com.alvaro.agentos.watcher.plist"
[ -f "$SRC" ] || { echo "no encuentro $SRC"; exit 1; }
mkdir -p "$HOME/Library/LaunchAgents"
cp "$SRC" "$DEST"
launchctl unload "$DEST" 2>/dev/null || true
launchctl load "$DEST"
launchctl start com.alvaro.agentos.watcher
echo "✅ Daemon AgentOS cargado y arrancado."
echo "   Vigila: inbox local (Cowork) + /idea de Telegram + git pull de alvaro-pipeline."
echo "   Logs:   tail -f state/watcher.out.log   (errores: state/watcher.err.log)"
echo "   Parar:  launchctl unload \"$DEST\""
