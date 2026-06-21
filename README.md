# AgentOS

> **Infraestructura de agentes autónoma y verificable — operativa 24/7 en producción.**  
> **→ [github.com/Wcoach24/agentos](https://github.com/Wcoach24/agentos)**

---

## Pitch de 60 segundos

**El problema:** La mayoría de los agentes saben *empezar* tareas. Terminarlas bien —con evidencia objetiva de que algo realmente está hecho— es el problema difícil.

**La solución:** AgentOS recibe una misión en YAML con un *Definition of Done* comprobable por máquina, la ejecuta en un bucle autónomo `plan → bookkeep → verify → route`, y **solo cierra cuando un verificador independiente** confirma checks objetivos: `http_status`, `file_exists`, `command_exit_zero`, `file_contains`. El agente nunca es juez y parte.

**En producción ahora mismo** — Mac Mini, 24/7, sin supervisión:

```
$ cat state/watcher_heartbeat.txt
1782051742  2026-06-21T16:22:22  pid=97368     # < 120s = sistema sano

$ ls missions/done/
2026-06-16-geo-es-dossier/
2026-06-17-catering-connect-foundation/
2026-06-17-confirm-e2e/
2026-06-17-test-hello-vercel/
2026-06-18-agentos-dashboard/
2026-06-18-spcx-short-watcher/
2026-06-20-aval-framework-spine/
# → 7 misiones cerradas de forma autónoma y verificada

$ tail -3 state/watcher.out.log
[runner] MISIÓN COMPLETADA: 2026-06-18-agentos-dashboard
[runner] MISIÓN COMPLETADA: 2026-06-17-test-hello-vercel
[runner] MISIÓN COMPLETADA: 2026-06-16-geo-es-dossier
```

**Para un reclutador:** LangGraph + Claude SDK + launchd daemon, 5 capas desacopladas, end-to-end en producción.  
**Para un VP Engineering:** verificador independiente (anti reward-hacking), gates GO/NO por Telegram, resumible tras crash via SQLite.  
**Para un inversor:** cualquier idea → YAML → resultado verificado sin supervisión. La infraestructura que convierte LLMs en trabajadores autónomos reales.

---

## Qué es

AgentOS recibe una **misión** (un YAML con objetivo + un *Definition of Done* comprobable) y la lleva sola hasta cerrarla. Cada misión pasa por un bucle:

```
plan  →  bookkeep  →  verify  →  route
```

- **plan** — el agente da el siguiente paso real hacia el objetivo (escribe ficheros, despliega, crea repos…).
- **bookkeep** — lleva la cuenta (coste, vueltas, no-progreso) y aplica topes anti-atasco.
- **verify** — un **verificador independiente** comprueba el *Definition of Done* con **checks de máquina** (no se fía del agente).
- **route** — ¿hecho? cierra. ¿gate humano? pide GO/NO por Telegram. ¿ni una cosa ni la otra? otra vuelta, con el feedback del verificador incorporado.

## Por qué importa

- **El "hecho" no es falseable.** Toda misión exige al menos un check de **máquina** (`file_exists`, `http_status`, `command_exit_zero`, `file_contains`). El juicio de un LLM (`agent_judgment`) puede *sumar* calidad, pero **nunca cierra una misión por sí solo**.
- **Verificador independiente.** Corre en contexto separado y en modo solo-lectura: no puede tocar los artefactos que juzga (anti reward-hacking). Ver [`orchestrator/verifier.py`](orchestrator/verifier.py).
- **Autónomo por defecto, humano donde toca.** Solo se detiene a pedir **GO/NO** ante **dinero** o algo **irreversible**. Desplegar a una URL pública, crear un repo de GitHub = autónomo.
- **No se atasca.** Topes de iteraciones, tiempo de pared, no-progreso y timeout por llamada al SDK garantizan que ninguna misión bloquea el sistema.
- **Resumible.** Todo el estado vive en checkpoints SQLite; si la máquina se reinicia, retoma donde iba.

---

## Arquitectura

Cinco capas desacopladas:

```
┌─────────────────────────────────────────────────────────────┐
│  BRIDGES (cómo entra una misión)                            │
│  GitHub API  /  Cowork (local)  /  Telegram /idea           │
└───────────────────────────┬─────────────────────────────────┘
                            ↓  missions/inbox/<id>.yaml
┌─────────────────────────────────────────────────────────────┐
│  WATCHER  (bin/watcher.py, daemon launchd)                  │
│  Cola serie + prioridad · retoma pausadas · heartbeat       │
└───────────────────────────┬─────────────────────────────────┘
                            ↓  missions/active/<id>/
┌─────────────────────────────────────────────────────────────┐
│  ORQUESTADOR  (orchestrator/graph.py, LangGraph)            │
│  plan → bookkeep → verify → route                           │
│  Checkpoints SQLite (resumible tras crash/rate-limit)       │
└──────────┬────────────────┬────────────────────────────────-┘
           ↓                ↓
┌──────────────┐   ┌──────────────────────────────────────────┐
│  MOTOR       │   │  VERIFICADOR  (orchestrator/verifier.py) │
│  Claude SDK  │   │  Checks de máquina (obligatorios)        │
│  plan Max    │   │  + agent_judgment (opcional, read-only)  │
│  sin API key │   │  Sin verde aquí → el grafo no cierra     │
└──────────────┘   └──────────────┬───────────────────────────┘
                                  ↓  si gate
                   ┌──────────────────────────────────────────┐
                   │  GATES  (orchestrator/gates.py)          │
                   │  Telegram botones GO/NO · email fallback │
                   │  interrupt() congela el grafo sin gastar │
                   └──────────────────────────────────────────┘
```

| Capa | Fichero clave | Rol |
|---|---|---|
| **Watcher** | [`bin/watcher.py`](bin/watcher.py) | Daemon launchd. Vigila la cola, lanza el runner, retoma pausadas, atiende `/idea` de Telegram. |
| **Orquestador** | [`orchestrator/graph.py`](orchestrator/graph.py) | Grafo LangGraph: `plan → bookkeep → verify → route`. Checkpoints SQLite. |
| **Motor** | [`orchestrator/engine.py`](orchestrator/engine.py) | Envuelve el Claude Agent SDK. Plan Max (sin API key). Captura coste real. |
| **Verificador** | [`orchestrator/verifier.py`](orchestrator/verifier.py) | Comprueba el DoD; mezcla checks de máquina (obligatorios) con juicio de modelo (opcional, read-only). |
| **Gates** | [`orchestrator/gates.py`](orchestrator/gates.py) | GO/NO por Telegram (botones) o email. `interrupt()` congela el grafo sin consumir cuota. |

### El ciclo de vida de una misión

```
missions/inbox/<id>.yaml      ← watcher la detecta
   → missions/active/<id>/   ← runner crea workspace, arranca grafo
      · gate     → interrupt → Telegram GO/NO → resume
      · rate-limit → _PAUSED.json + exit 75 → watcher retoma
   → missions/done/<id>/     ← finalize: workspace + _RESULT.json
   → missions/_processed/    ← yaml archivado; aviso Telegram
```

El agente trabaja SOLO en su workspace (`missions/active/<id>/`). El entregable se nombra como pide la DoD; el sistema lo mueve a `done/` al cerrar.

### Los 3 bridges

| Origen | Cómo entra | Para qué |
|--------|------------|----------|
| **GitHub API** | commit en `inbox/` → watcher hace pull por API | misiones desde claude.ai sin acceso al Mac |
| **Cowork (local)** | `bin/new_mission.py` escribe directo en inbox | trabajar la idea con acceso completo al Mac |
| **Telegram `/idea`** | `bin/dispatcher.py` destila idea→YAML→inbox | rápido, desde el móvil |

---

## Cómo correrlo

### 1. Requisitos

- Python 3.10+
- `claude` CLI (Claude Code) con sesión Max activa (login una vez)
- Token de Telegram (para gates y notificaciones)
- `gh` CLI autenticado (para el bridge GitHub)

### 2. Instalación

```bash
git clone https://github.com/Wcoach24/agentos.git
cd agentos

# Entorno virtual
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Credenciales
cp .env.example .env
# Edita .env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, GITHUB_REPO, ...

# Login del plan Max (una vez; sin esto el SDK no corre)
claude
```

### 3. Verificar que todo está en orden

```bash
python smoke_test.py        # fontanería: TODO VERDE sin gastar crédito
bash bridge_check.sh        # qué falta para el bridge (git auth, daemon, Telegram)
```

### 4. Lanzar el daemon (watcher)

```bash
bash install_daemon.sh      # instala y arranca el daemon launchd
tail -f state/watcher.out.log   # logs en tiempo real
```

### 5. Enviar una misión

```bash
# Opción A: fichero YAML local
python bin/new_mission.py missions/ejemplos/hello-world.yaml

# Opción B: desde Telegram
/idea "Publica una landing con el resumen de mi CV en vercel.app"

# Opción C: desde claude.ai (bridge git)
# Escribe la misión en un chat de claude.ai → "continúalo solo" → el Mac la coge
```

### 6. Monitorizar

```bash
python dashboard.py                      # estado: inbox / active / done
tail -f state/watcher.out.log            # logs del daemon
cat state/watcher_heartbeat.txt          # liveness check (< 120s = sano)
```

---

## Formato de misión

```yaml
id: 2026-06-17-landing-esgeo        # slug único con fecha
title: "Landing esGEO v2"
objective: "Una frase: qué debe ser verdad al terminar."
context: "Lo destilado del hilo."
done_level: staging                  # staging | production

definition_of_done:                  # >= 1 check de MÁQUINA obligatorio
  - id: url-viva
    check: "Responde 200 en su URL pública"
    verify: { type: http_status, target: "https://x.vercel.app", expected: "200" }
  - id: cta-visible
    check: "El CTA está en la página"
    verify: { type: file_contains, target: "index.html", expected: "Solicitar demo" }

budget: { max_iterations: 20, credit_usd: 5.0, no_progress_limit: 4, wall_clock_hours: 6 }
gates: { payment: true, irreversible: true }
```

**Tipos de verify:** `file_exists`, `http_status`, `command_exit_zero`, `file_contains` (checks de máquina — obligatorios), y `agent_judgment` (juicio de modelo — opcional, nunca cierra solo).

---

## Evidencia en producción

Snapshot tomado el 2026-06-21 del sistema corriendo en el Mac Mini:

```
# Watcher vivo (pid 97368 activo, heartbeat < 120s)
$ cat state/watcher_heartbeat.txt
1782051742  2026-06-21T16:22:22  pid=97368

# 7 misiones cerradas autónomamente
$ ls missions/done/
2026-06-16-geo-es-dossier/
2026-06-17-catering-connect-foundation/
2026-06-17-confirm-e2e/
2026-06-17-test-hello-vercel/
2026-06-18-agentos-dashboard/
2026-06-18-spcx-short-watcher/
2026-06-20-aval-framework-spine/

# Últimas líneas del log del daemon
$ tail -3 state/watcher.out.log
[runner] MISIÓN COMPLETADA: 2026-06-18-agentos-dashboard
[runner] MISIÓN COMPLETADA: 2026-06-17-test-hello-vercel
[runner] MISIÓN COMPLETADA: 2026-06-16-geo-es-dossier
```

Los ficheros clave que implementan lo descrito arriba:

| Fichero | Qué hace |
|---------|----------|
| [`bin/watcher.py`](bin/watcher.py) | Cola, heartbeat, launchd |
| [`orchestrator/graph.py`](orchestrator/graph.py) | Grafo LangGraph completo |
| [`orchestrator/verifier.py`](orchestrator/verifier.py) | Todos los tipos de check |
| [`orchestrator/engine.py`](orchestrator/engine.py) | Wrapper Claude SDK |
| [`orchestrator/gates.py`](orchestrator/gates.py) | GO/NO por Telegram |

---

## Diseño y decisiones

- **¿Por qué serie?** Una misión a la vez protege la cuota Max compartida; el Claude interactivo sigue respondiendo mientras el daemon trabaja en segundo plano.
- **¿Por qué sin API key?** El SDK usa el token OAuth del plan Max. Cero coste adicional para misiones normales; el daemon se autopausa si toca el límite de uso.
- **¿Por qué verificador independiente?** Para evitar reward hacking: si el agente pudiera modificar los tests, "pasar" el DoD sería trivial y vacío. El verificador es read-only.
- **¿Por qué LangGraph + SQLite?** Resumibilidad sin infraestructura. El checkpoint sobrevive reinicios, apagones y rate-limits sin perder progreso.
- **¿Por qué `ps` y no `pgrep` para el health-check?** En macOS, `pgrep` excluye sus propios procesos ancestros. El watcher es ancestro del verificador, así que `pgrep` da falso negativo. `ps aux | grep watcher` detecta el proceso correctamente.

---

## Estado actual

- ✅ End-to-end real confirmado en Mac Mini (24/7)
- ✅ Watcher con heartbeat fiable bajo launchd (KeepAlive)
- ✅ Verificador independiente + DoD de máquina
- ✅ Gates por Telegram con botones GO/NO
- ✅ Idempotencia: cada misión corre exactamente una vez
- ✅ Re-runs limpios (checkpoint se borra en re-run fresco)
- ✅ 7 misiones cerradas autónomamente y verificadas
- ✅ Repo público: [github.com/Wcoach24/agentos](https://github.com/Wcoach24/agentos)

## Hoja de ruta

- Gate nativo por hook del SDK (PreToolUse) para dinero/irreversible
- Métricas en SQLite (Autonomy Index, DoD pass rate)
- Verificación anti-Potemkin (Playwright: clic real, no solo HTTP 200)
- Hash de no-progreso que incluye el veredicto del verificador (anti reward-hacking reforzado)
- Paralelismo controlado: N misiones simultáneas con cuota compartida

---

## Licencia

MIT. Construido y operado por [Álvaro](https://github.com/Wcoach24) como infraestructura personal de agentes.
