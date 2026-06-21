# AgentOS — Arquitectura (referencia viva)

> Documento de referencia del sistema. Para el log cronológico de cambios ver `CHANGES.md`.
> Para el backlog de mejoras ver `docs/ROADMAP.md`. Última revisión: 2026-06-17.

## 1. Qué es y por qué existe

AgentOS es un sistema de agentes autónomos que vive en un Mac Mini (24/7). Convierte una
idea trabajada en un chat en algo **terminado y corriendo**, no solo planificado,
consultando al humano SOLO en pagos y acciones irreversibles.

**Filosofía central (no perderla de vista):** lo más importante NO es lanzar agentes, es
**pararlos bien**. La *Definition of Done* verificable y el verificador independiente son
el corazón. Un agente que "casi termina" no vale; el sistema solo cierra cuando hay
evidencia objetiva de que la cosa de verdad funciona.

Esto es el antídoto al patrón "construir al 95% y no terminar/distribuir": el sistema
llega hasta el *done* verificado mientras el humano no está en el bucle para abandonarlo.

## 2. Las 5 capas

1. **Handoff** — una idea se destila en un `mission.yaml` (objetivo + DoD verificable +
   presupuesto + política de gates) y aterriza en `missions/inbox/`.
2. **Watcher** (`bin/watcher.py`, daemon launchd) — vigila la inbox, arranca el runner,
   retoma misiones pausadas, atiende `/idea` de Telegram y hace `git pull` del repo puente.
3. **Orquestador** (`orchestrator/graph.py`, LangGraph) — el bucle como grafo de estados
   con checkpoint SQLite (resumible tras crash/reinicio).
4. **Verificador** (`orchestrator/verifier.py`) — comprueba la DoD contra evidencia
   objetiva. Sin su visto bueno el grafo no cierra: vuelve a planificar.
5. **Gates + Notificación** (`orchestrator/gates.py`) — pagos / irreversibles CONGELAN el
   grafo (LangGraph `interrupt`, sin gastar crédito), avisan por Telegram (botones GO/NO)
   y esperan la decisión. Done/abortada/pausa llegan también por Telegram.

## 3. El grafo (orquestador)

```
START → plan → bookkeep → verify → (route)
                                      ├─ done ........... → END (finalize → missions/done/)
                                      ├─ aborted ........ → END (finalize ok=false + aviso)
                                      ├─ pending_gate ... → gate_notify → gate_wait → plan
                                      └─ si no .......... → plan (otra vuelta)
```

- **plan** (`node_plan`): construye el prompt desde la misión (objetivo, contexto, DoD,
  restricciones) + **realimentación del verificador** (los checks que fallaron la vuelta
  anterior, para autocorregirse) y lanza el Claude Agent SDK con las **skills del usuario**
  cargadas. Detecta `GATE:` en la salida; si el SDK topa rate-limit, lanza `RateLimitPause`.
- **bookkeep** (`node_bookkeep`): contabilidad por vuelta. Acumula el **coste REAL** del SDK
  (`ResultMessage.total_cost_usd`), lleva el hash de no-progreso, y aplica los TOPES
  (max_iterations / 80% del crédito / no-progreso). Si alguno salta, marca `aborted`.
- **verify** (`node_verify`): si hay gate o abort pendiente, se salta (no gasta SDK). Si no,
  corre el verificador sobre la DoD. Devuelve `done` + `verifier_results`.
- **route**: decisión PURA (sin efectos): aborted/done → END; pending_gate → gate; si no → plan.
- **gate_notify / gate_wait**: separados a propósito. `interrupt()` re-ejecuta su nodo entero
  al reanudar, así que el envío del email/Telegram vive en `gate_notify` (no se duplica) y
  el `interrupt()` en `gate_wait`.

**Por qué ejecución SÍNCRONA:** `interrupt()` necesita el contexto del runnable, que no se
propaga en async bajo Python < 3.11; y el `SqliteSaver` síncrono no soporta async. La ruta
síncrona funciona en 3.10 y 3.14. Las llamadas async del SDK se puentean con `run_sync()`.

## 4. Anatomía de una misión

```yaml
id: 2026-06-17-landing-esgeo        # slug único con fecha
title: "Landing esGEO v2"            # <=80
objective: "Una frase: qué debe ser verdad al terminar."
context: "Lo destilado del hilo."
done_level: staging                  # staging (URL gratis) | production (dominio, gates)
priority: 0                          # mayor salta la cola (serie)
skills: all                          # all | ["gsd-pro","impeccable",...] | []
definition_of_done:                  # OBLIGATORIO >=1 check de MÁQUINA
  - id: dod-1
    check: "Responde 200 en su URL pública"
    verify: { type: http_status, target: "https://x.vercel.app", expected: "200" }
constraints: ["no publicar en redes", "no gastar dinero"]
budget: { max_iterations: 20, credit_usd: 5.0, no_progress_limit: 4, wall_clock_hours: 6 }
gates: { payment: true, irreversible: true }
```

**Tipos de verify:** `file_exists`, `http_status`, `command_exit_zero`, `file_contains`
(checks de MÁQUINA), y `agent_judgment` (juicio de un subagente). Regla dura: toda misión
necesita ≥1 check de máquina; `agent_judgment` SUMA calidad pero NUNCA cierra solo. Lo
impone `runner.validate_mission` (rechaza la misión si no cumple).

## 5. Ciclo de vida de una misión

```
missions/inbox/<id>.yaml   (encolada)
   → watcher la mueve a missions/active/<id>.yaml + crea workspace missions/active/<id>/
   → runner corre el grafo (checkpoint en state/checkpoints.sqlite, thread_id=<id>)
      · gate     → interrupt → Telegram GO/NO → resume
      · rate-limit → _PAUSED.json + exit 75 → el watcher retoma tras PAUSE_BACKOFF
   → al cerrar: finalize mueve el workspace a missions/done/<id>/ + _RESULT.json
   → watcher mueve el yaml a missions/_processed/ + aviso Telegram (COMPLETADA/DETENIDA)
```

El workspace de la misión ES el cwd del agente. El agente trabaja SOLO ahí (el prompt lo
confina); el entregable se nombra como pide la DoD y el sistema lo mueve a `done/` al cerrar.

## 6. Componentes (mapa de ficheros)

```
orchestrator/
  engine.py     SDK Claude Agent (plan Max, sin API key). run_agent: opts defensivas
                (pasa skills/setting_sources/can_use_tool solo si el SDK los soporta),
                captura coste real + is_error + rate_limited, try/except a prueba de fallos.
  graph.py      grafo LangGraph: nodos plan/bookkeep/verify/gate_*, route puro, RateLimitPause.
  runner.py     ejecuta una misión; validate_mission; interrupt/resume; pausa resumible (exit 75);
                finalize (active→done + _RESULT.json).
  verifier.py   checks de máquina (puros) + agent_judgment (subagente opus, READ-ONLY).
  gates.py      Telegram (botones GO/NO, offset idempotente) + email fallback; /idea routing; notify.
bin/
  watcher.py    daemon: inbox serie+prioridad, retoma pausadas, /idea de Telegram, git pull+auto-clone.
  new_mission.py  bridge local: valida + encola un mission.yaml (lo usa Cowork).
  dispatcher.py   bridge Telegram /idea: destila idea→misión con el SDK, valida, encola.
install/
  com.alvaro.agentos.watcher.plist   daemon launchd (rutas reales + PATH claude + carga .env).
schemas/mission.schema.json          contrato de la misión.
dashboard.py                          vista de estado (inbox/active/done + checkpoint).
smoke_test.py                         valida la fontanería sin gastar crédito (TODO VERDE).
run.sh / confirm.sh / install_daemon.sh / bridge_check.sh   operativa.
docs/                                 BRIDGE.md, CLAUDEAI_BRIDGE.md, ARCHITECTURE.md, ROADMAP.md.
```

## 7. Los 3 bridges (cómo entra una misión)

Todo termina en `missions/inbox/`. Cualquier cosa que deje ahí un `mission.yaml` válido es
un bridge válido.

| Origen | Cómo entra | Quién destila | Para |
|--------|-----------|---------------|------|
| **claude.ai + git** | commit a `alvaro-pipeline/missions/inbox/` → `git pull` | claude.ai | ideas trabajadas a fondo |
| **Cowork** | escribe directo en inbox (`new_mission.write_mission`) | Claude en Cowork | trabajar la idea con acceso al Mac |
| **Telegram `/idea`** | el Mac destila con el SDK (`dispatcher`) → inbox | el SDK del Mac | rápido, desde el móvil |

El SDK del Mac NUNCA habla con el chat: lee el `mission.yaml`. El chat solo lo PRODUCE.

## 8. Auth y coste

- Motor: Claude Agent SDK bajo el **plan Max** (login OAuth del CLI `claude`). **Nunca**
  `ANTHROPIC_API_KEY` (el sistema la borra del proceso). El SDK consume de la **cuota Max
  compartida** (el pool de crédito aparte se pausó el 15-jun-2026; no hay crédito que reclamar).
- Por eso una misión desbocada puede comerse tu Claude interactivo → topes por misión
  (`credit_usd`, autopausa 80%) + max_iterations + no-progreso + pausa por rate-limit.
- ⚠️ Riesgo a vigilar: usar el token OAuth del plan vía SDK fuera de Claude Code podría
  rozar los Consumer ToS de Anthropic. Verificar antes de escalar a 24/7 (ver ROADMAP).

## 9. Invariantes (lo que NO hay que romper en futuras mejoras)

- **El verificador es el corazón.** Nunca debilitarlo para forzar un verde. Si algo no cierra,
  arregla el agente o el contrato, no relajes el check.
- **≥1 check de máquina por misión.** `agent_judgment` nunca cierra solo.
- **Realimentación verificador→planificador.** Sin ella un check estricto haría loop infinito.
- **El verificador es independiente y READ-ONLY** (no puede tocar artefactos/tests → anti
  reward-hacking).
- **Gates solo para dinero / irreversible-grave.** URLs y repos públicos = autónomo.
- **Coste real, no estimado.** Los topes se aplican sobre `total_cost_usd` del SDK.
- **Nunca `ANTHROPIC_API_KEY`.**
- **Side-effects en el nodo previo al `interrupt()`** (no en el que interrumpe; se re-ejecuta).
```
