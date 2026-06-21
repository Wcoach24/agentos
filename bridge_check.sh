
#!/usr/bin/env bash
# bridge_check.sh — verifica de una pasada que el bridge claude.ai está listo y
# te dice EXACTAMENTE qué falta. Correr en el Mac:  cd ~/Desktop/os/agent-os && bash bridge_check.sh
cd "$(dirname "$0")"
OK="✅"; NO="❌"; WARN="⚠️ "
miss=0

echo "==================================================="
echo "  AgentOS — chequeo del bridge"
echo "==================================================="

# 0) .env
if [ -f .env ]; then set -a; source .env; set +a; echo "$OK  .env cargado"
else echo "$NO  falta .env"; miss=$((miss+1)); fi

# 1) venv + smoke (fontanería)
if [ -d .venv ]; then
  if .venv/bin/python smoke_test.py >/dev/null 2>&1; then echo "$OK  entorno Python + smoke TODO VERDE"
  else echo "$NO  smoke falla -> .venv/bin/pip install -r requirements.txt"; miss=$((miss+1)); fi
else echo "$NO  falta .venv -> python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"; miss=$((miss+1)); fi

# 2) CLI 'claude' (login del plan Max)
if command -v claude >/dev/null 2>&1; then echo "$OK  CLI 'claude' en PATH ($(command -v claude))"
else echo "$NO  'claude' no está en PATH -> npm install -g @anthropic-ai/claude-code && claude (login)"; miss=$((miss+1)); fi

# 3) Telegram (el token responde)
if [ -n "${TELEGRAM_BOT_TOKEN:-}" ]; then
  if curl -s --max-time 10 "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getMe" | grep -q '"ok":true'; then
    echo "$OK  Telegram OK (bot responde; chat ${TELEGRAM_CHAT_ID:-?})"
  else echo "$NO  Telegram no responde -> revisa TELEGRAM_BOT_TOKEN en .env"; miss=$((miss+1)); fi
else echo "$NO  falta TELEGRAM_BOT_TOKEN en .env"; miss=$((miss+1)); fi

# 4) acceso al repo puente según el modo (github_api recomendado | git | local)
if [ "${REPO_SYNC:-local}" = "github_api" ]; then
  api="https://api.github.com/repos/${GITHUB_REPO}/contents/${MISSIONS_PATH:-missions/inbox}"
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 -H "Authorization: Bearer ${GITHUB_TOKEN}" -H "Accept: application/vnd.github+json" "$api")
  if [ "$code" = "200" ]; then echo "$OK  API GitHub OK (lee $GITHUB_REPO/${MISSIONS_PATH:-missions/inbox} con el PAT)"
  else echo "$NO  API GitHub devolvió HTTP $code -> revisa GITHUB_TOKEN/GITHUB_REPO en .env"; miss=$((miss+1)); fi
elif [ "${REPO_SYNC:-local}" = "git" ]; then
  if GIT_TERMINAL_PROMPT=0 git ls-remote "$REPO_URL" >/dev/null 2>&1; then echo "$OK  git accede al repo puente ($REPO_URL)"
  else echo "$NO  git NO puede clonar $REPO_URL -> gh auth login"; miss=$((miss+1)); fi
else echo "${WARN}REPO_SYNC=local (solo Cowork/Telegram alimentan la inbox)"; fi

# 5) daemon cargado
if launchctl list 2>/dev/null | grep -q com.alvaro.agentos.watcher; then echo "$OK  daemon del watcher cargado"
else echo "$NO  daemon NO cargado -> bash install_daemon.sh"; miss=$((miss+1)); fi

echo "---------------------------------------------------"
if [ "$miss" -eq 0 ]; then
  echo "$OK  TODO LISTO en el Mac. Solo falta pegar el snippet en las preferencias de claude.ai."
  echo "     Prueba: di 'continúalo solo' en claude.ai y mira tu Telegram."
else
  echo "$NO  faltan $miss cosa(s) marcadas arriba. Arréglalas y repite: bash bridge_check.sh"
fi
echo "==================================================="
