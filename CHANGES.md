# AgentOS — cambios aplicados durante la instalación/prueba (2026-06-16)

Entorno de prueba: sandbox Linux (Python 3.10.12) con acceso a la carpeta `os`.
Versiones instaladas: `langgraph 1.2.5`, `langgraph-checkpoint-sqlite 3.1.0`,
`langgraph-checkpoint 4.1.1`, `langchain-core 1.4.7`, `claude-agent-sdk 0.2.102`,
`anyio 4.14.0`, `pyyaml 6.0.3`.

## Qué estaba bien (sin tocar)
- `engine.py`: la API del SDK (`query`, `ClaudeAgentOptions`, `AssistantMessage`,
  `TextBlock`) y los campos de `ClaudeAgentOptions` (`system_prompt`, `allowed_tools`,
  `permission_mode`, `max_turns`, `cwd`, `model`) coinciden con la 0.2.102. No hubo drift.
- `verifier.py`, `gates.py`, `dashboard.py`, `schemas/`, `smoke_test.py`: sin cambios.
- Imports de langgraph (`StateGraph/START/END`, `SqliteSaver`, `interrupt`, `Command`): válidos.

## Bug 1 (bloqueante FASE 3) — ejecución async incompatible
**Síntoma:** `runner.py` invocaba el grafo con `await app.ainvoke(...)` pero `graph.py`
usaba el `SqliteSaver` **síncrono**, que lanza `NotImplementedError` en métodos async.
Aun cambiando a `AsyncSqliteSaver`, `interrupt()` fallaba con
`RuntimeError: Called get_config outside of a runnable context`: en langgraph 1.2.5
`get_config()` depende de un *contextvar* que **no se propaga en async bajo Python < 3.11**
(verificado leyendo `langgraph/config.py`). La ruta async estaba doblemente rota.

**Fix (robusto en 3.10 y 3.11+):** ejecutar el grafo de forma **síncrona**.
- `graph.py`: `node_plan` y `node_verify` pasan de `async def` a `def` y puentean las
  llamadas async del SDK/verificador con un helper `run_sync(coro) = asyncio.run(coro)`
  (loop efímero por nodo; seguro porque `invoke()` corre sin loop activo).
  Se mantiene el `SqliteSaver` síncrono (que es lo que `interrupt()` necesita).
- `runner.py`: `run_mission` y `_drive` pasan a síncronos; `ainvoke`→`invoke`;
  se elimina `anyio.run` en `main()` y el import de `anyio`.

## Bug 2 (correctitud del gate) — email [GATE] duplicado
**Síntoma:** al reanudar tras un gate, langgraph **re-ejecuta el nodo entero** que llamó
`interrupt()`, así que `send_gate()` se enviaba dos veces (verificado en test e2e).
**Fix:** separar el nodo `gate` en dos:
- `node_gate_notify` → envía el email UNA vez (queda *checkpointed*, no se re-ejecuta).
- `node_gate_wait` → llama `interrupt()` y procesa la decisión (este sí se re-ejecuta).
Aristas: `verify --(gate)--> gate_notify -> gate_wait -> plan`.

## Verificación (sin gastar crédito)
- `smoke_test.py` → **TODO VERDE**.
- `dashboard.py` → la misión `2026-06-16-geo-es-dossier` aparece en INBOX.
- Test e2e propio con SDK y email **stubbeados** (cero crédito, cero correos reales):
  ejercita las 5 capas — handoff (carga yaml) → loop (plan/verify) → verificador
  (objetivo file_exists/file_contains) → gate (interrupt/resume con [GO]) →
  checkpoint (thread persistido en SQLite) → notificación (`send_gate` COMPLETADA).
  Resultado: misión cerrada `ok=true`, deliverable movido a `missions/done/`, y
  **email de gate enviado exactamente 1 vez**.

## Notas para correr en el Mac Mini
- `requirements.txt` no necesita cambios (la ruta síncrona no usa `aiosqlite`).
- El `.venv` se crea en tu Mac; el venv de prueba era del sandbox Linux (no portable).
- Regla de auth respetada: `ANTHROPIC_API_KEY` NO está definida; `engine.py` además la
  borra del entorno del proceso por seguridad.

---

## Primera misión real (2026-06-16, run 1) — diagnóstico y 4 fixes más

La misión corrió en el Mac (Python 3.14, plan Max autenticado). El agente PRODUJO un
dossier bueno (`DOSSIER.md`, `EVIDENCE.md`, `STATUS-final.md` con competidores+URLs 200,
pricing con fuente, veredicto GO condicional y 3 riesgos). Pero el verificador NUNCA lo
cerró y al final el runner petó. Causas (todas reales):

3. **cwd desaparecido → crash.** El agente, al ver el DoD apuntando a
   `missions/done/<id>/`, escribió ahí (raíz del repo) y se cargó su propio workspace
   `missions/active/<id>`. Cuando el verificador (agent_judgment) lanzó el SDK con
   cwd=ese workspace → `CLIConnectionError: Working directory does not exist`.
   FIX engine.py: `os.makedirs(cwd, exist_ok=True)` antes de lanzar el subproceso.
4. **DoD con ruta incoherente.** Los target del DoD eran `missions/done/<id>/DOSSIER.md`
   pero el verificador los une al cwd (workspace). FIX mission.yaml: target relativo al
   workspace (`DOSSIER.md`); el sistema ya lo mueve a missions/done al cerrar.
5. **file_contains demasiado literal.** El agente tituló `## 2. Competidores GEO` y el
   DoD buscaba `## Competidores` (0 match). FIX: expected → `Competidores`/`Pricing`
   (presencia de sección) + el prompt ahora pide encabezados exactos sin numerar.
6. **Detector de no-progreso roto.** `_hash_state` incluía `iteration`, que cambia cada
   vuelta → el hash nunca se repetía → nunca detectaba estancamiento → loop/coste runaway.
   FIX: hashear solo `last_action`.

Además: prompt de node_plan ahora confina al agente a su directorio actual (no tocar
missions/ ni subir a padres), y el plist del daemon corregido (rutas reales + PATH con
~/.hermes/node/bin + carga de .env).

Verificado tras los fixes: smoke TODO VERDE + e2e stub (5 capas) verde, con entregable
en done/, gate 1 sola vez y notificación COMPLETADA. Falta el re-run real para auto-
certificar (decisión de Álvaro: cuesta algo de crédito del plan).

---

## Alineación con el objetivo real (2026-06-16, run 2) — el verificador es el corazón

Contexto nuevo de Álvaro: el núcleo del sistema NO es lanzar agentes, es PARARLOS bien
(DoD verificable + verificador independiente). Con esa lente, revierto y corrijo:

7. **REVERTIDO el debilitamiento del verificador.** Ayer relajé `## Competidores` →
   `Competidores` para forzar un verde: dirección equivocada. Vuelve a estricto
   (`## Competidores` / `## Pricing`).
8. **Bucle de feedback verificador→planificador (FALTABA).** Los `verifier_results` se
   guardaban pero NO se le pasaban al agente, así que un check estricto haría loop
   infinito sin saber por qué. Ahora node_plan inyecta los checks fallidos en el prompt
   ("corrige EXACTAMENTE esto"). Probado: el agente escribe mal en la vuelta 1, el
   verificador falla, el feedback llega, y se autocorrige en la vuelta 2.
9. **Nodo `bookkeep` (FALTABA en el grafo).** La arquitectura define
   `plan -> bookkeep -> verify -> route` pero estaba `plan -> verify -> route`. Añadido.
   bookkeep: (a) acumula el COSTE REAL del SDK (`ResultMessage.total_cost_usd`) en vez
   del `+$0.10` ficticio → los topes (credit_usd, autopausa 80%) por fin son de verdad;
   (b) lleva el hash de no-progreso; (c) aplica los topes (iteraciones / 80% crédito /
   no-progreso). `route()` queda como decisión PURA. `verify` se salta si hay abort
   pendiente (no malgasta una llamada de SDK).
10. **Paso de agente a prueba de fallos.** `run_agent` captura el coste real y envuelve
    el SDK en try/except: un error del SDK ya NO revienta el runner; se registra como
    paso erróneo y el no-progreso lo cierra bien con email. (engine.py: EngineResult
    gana cost_usd/is_error/num_turns/error.)

Verificado: smoke TODO VERDE (incl. test de bookkeep con coste real) + e2e bucle de
feedback (cierre estricto) + e2e gate (interrupt/resume, 1 email, COMPLETADA). Grafo:
plan, bookkeep, verify, gate_notify, gate_wait.

---

## Cambios del grill (2026-06-16, run 3) — alineación operativa

Tras grillar el objetivo, decisiones tomadas y aplicadas:

11. **Schema:** `done_level` (staging|production) y `priority` (serie con cola). La DoD
    documenta que exige >=1 check de máquina.
12. **Validación dura del verificador:** `runner.validate_mission` rechaza toda misión
    cuya DoD no tenga >=1 check de máquina (file_exists/http_status/command_exit_zero/
    file_contains). agent_judgment NUNCA cierra solo. Crítico porque el handoff es
    auto-go (no revisas el yaml). Test en smoke.
13. **Gates estrechados:** el prompt del planificador ahora gatea SOLO dinero /
    irreversible-grave (borrar datos, publicar a audiencia real, email a terceros) /
    captcha-otp. Desplegar a URL pública gratis y crear/empujar repos (públicos o
    privados) = AUTÓNOMO.
14. **Pausa por rate-limit (resumible):** el SDK comparte la cuota Max (el pool aparte
    se pausó el 15-jun). engine detecta rate-limit; node_plan lanza `RateLimitPause`;
    el runner NO finaliza, marca `_PAUSED.json`, avisa por email y sale con código 75;
    el watcher la retoma tras `PAUSE_BACKOFF` (15 min). No se pierde progreso.
15. **Watcher serie + prioridad + retoma:** ordena el inbox por `priority`, retoma
    pausadas antes de empezar nuevas. Bridge explícito: REPO_SYNC=local por defecto
    (Cowork escribe el yaml validado directo en missions/inbox/ al decir "continúalo
    solo"); git opcional para chats sin acceso al Mac.
16. **INSTALL.md paso 4 corregido:** no hay crédito que reclamar; cuota Max compartida.

Avisos (confirmado): email en done / gate / abortada / pausa-1x; silencio en lo demás.
Verificado: smoke TODO VERDE (5 checks) + e2e feedback + e2e gate + e2e rate-limit-pause.

---

## Implementación del research (2026-06-17, run 4)

17. **Skills del usuario cargadas en el loop (fix nº1 del research).** `engine.run_agent`
    ahora acepta `setting_sources`/`skills`/`can_use_tool` y los pasa SOLO si tu SDK los
    soporta (builder defensivo por `dataclasses.fields`). node_plan pasa
    `setting_sources=["user","project"]` + `skills="all"` (override por misión vía campo
    `skills`). Verificado: ClaudeAgentOptions(skills="all") construye OK en 0.2.102.
18. **Verificador independiente, opus, READ-ONLY.** `_agent_judgment` ahora corre con
    `model="opus"` y `allowed_tools=["Read","Glob","Grep"]` (sin Bash) → el juez no puede
    tocar artefactos/tests (anti reward-hacking) y juzga con contexto separado del planner.
19. **Bridge formalizado.** `bin/new_mission.py` (`write_mission`) valida + encola en
    inbox = el "botón continúa solo" en modo local (Cowork escribe directo). `docs/BRIDGE.md`
    documenta los 3 sitios y los 2 modos (local/git). Probado: válida encola, inválida
    (sin check de máquina) rechazada.

Verificado: smoke TODO VERDE (5) + e2e feedback + e2e pause + construcción real de opts SDK.
Pendiente (necesita tu input / credenciales): gate nativo por hook, métricas, canal HITL.

---

## Telegram + confirmación (2026-06-17, run 5)

20. **Canal de gates por TELEGRAM (CONFIRMADO EN VIVO).** `gates.py` reescrito: Telegram
    como canal principal (botones inline ✅GO/❌NO, consumo idempotente por offset en
    `getUpdates` -> exactly-once, mata la fragilidad del IMAP), email como fallback.
    Elegible con `GATE_CHANNEL`. node_gate_notify manda el [GATE] con botones (decision=True);
    done/abortada/pausa van como notificación sin botones. Probado de VERDAD contra el
    chat de Álvaro: sendMessage ok (message_id 111) + ROUND-TRIP ok (pulsó GO -> capturado,
    approved=True). Credenciales en .env (no en el repo).
21. **Confirmación e2e turnkey.** `missions/inbox/2026-06-17-confirm-e2e.yaml` (misión
    mínima: crea HELLO.txt con un token, 3 checks de máquina, sin web/dinero/gates) +
    `confirm.sh` (un comando: corre el SDK real y dice ✅/❌). Es el único paso que falta
    para declarar el sistema "terminado": correrlo en el Mac (el SDK necesita el plan Max;
    en el sandbox no hay credenciales -> mismo is_error que el run 1, confirmando que aquel
    fallo era auth y no el código).

Estado: plomería verde (smoke + e2e) · canal HITL Telegram CONFIRMADO en vivo · bridge
probado. Falta: 1 corrida real del SDK en el Mac (confirm.sh) para cerrar el círculo.

---

## Bridge claude.ai (git) — la idea original de Álvaro (2026-06-17, run 6)

22. **Tres orígenes, una inbox.** (a) Cowork escribe directo; (b) Telegram `/idea` -> el Mac
    destila con el SDK (`bin/dispatcher.py`: idea -> mission.yaml validado -> inbox) y avisa;
    (c) **claude.ai + conector GitHub** commitea el yaml a un repo y el watcher hace `git pull`.
23. **Watcher git endurecido:** `_sync_repo` ahora VALIDA cada misión del repo (misma barrera
    que el runner: rechaza DoD sin check de máquina), DEDUP (`_seen` en inbox/active/_processed/done)
    y avisa por Telegram de recibida/rechazada. El SDK nunca habla con el chat: lee el yaml.
24. **Bridge Telegram `/idea`:** el watcher, ocioso, atiende `/idea ...` (poller único; los
    comandos que llegan durante un gate se reencolan, sin choque de offset). Probado con stub.
25. **docs/CLAUDEAI_BRIDGE.md:** flujo + setup (repo, clon, REPO_SYNC=git) + el snippet de
    preferencias de claude.ai que genera misiones válidas. docs/BRIDGE.md cubre el modo local.

Verificado: smoke verde + dispatcher (idea->misión válida encolada, stub) + ruteo de comandos
+ watcher importa/dedup. REPO_SYNC sigue en 'local' (git es opt-in cuando montes el repo+clon).

---

## Todo preparado para el bridge claude.ai (2026-06-17, run 7)

26. **Repo puente listo:** creada `missions/inbox/README.md` en Wcoach24/alvaro-pipeline
    (commit aede179) vía el conector GitHub (no el PAT del pipeline). Carpeta lista para
    que claude.ai deje misiones al decir "continúalo solo".
27. **Auto-clonado:** el watcher, en modo git, clona el repo en REPO_DIR si no existe
    (REPO_URL en .env) -> cero pasos de clonado manual. Falla con gracia + aviso Telegram
    si la auth de git del Mac no está lista (local + Telegram siguen).
28. **.env en modo git:** REPO_SYNC=git, REPO_URL=.../alvaro-pipeline, REPO_DIR=~/agent-os-pipeline.
    (El modo local de Cowork sigue activo en paralelo: ambos alimentan la inbox.)
29. **install_daemon.sh:** un comando carga el daemon launchd (plist ya con rutas reales +
    PATH de claude + carga de .env). docs/CLAUDEAI_BRIDGE.md tiene el snippet de preferencias.

Para Álvaro quedan SOLO 2 cosas: (1) pegar el snippet en las preferencias de claude.ai;
(2) `bash install_daemon.sh` una vez (launchctl es del Mac, no automatizable desde aquí).

---

## Documentación + bridge_check (2026-06-17, run 8)

30. **bridge_check.sh:** chequea de una pasada .env, venv+smoke, CLI claude, Telegram (getMe),
    auth de git al repo puente, daemon cargado y clon — y dice qué falta. Probado: detecta
    Telegram OK en vivo.
31. **Documentación viva** para iterar el sistema:
    - `README.md` — índice + arranque rápido + los 3 bridges + cómo mejorar.
    - `docs/ARCHITECTURE.md` — las 5 capas, el grafo, componentes, ciclo de vida, auth/coste,
      e **invariantes que no romper** (el verificador es el corazón, ≥1 check de máquina, etc.).
    - `docs/ROADMAP.md` — backlog priorizado (impacto/esfuerzo/estado) + el bucle de versionado.
    Marca esto como **v1.0** (e2e real confirmado).

---

## Audit + automatización total (2026-06-17, run 9)

32. **Audit:** claude.ai commiteó una misión VÁLIDA al repo (catering-connect, 11 checks,
    varios de máquina) -> el bridge funciona. El motor funciona (confirm-e2e ok). PERO el
    daemon NO estaba corriendo (sin watcher.out/err.log) -> nadie hacía pull -> 0 resultados.
33. **Visibilidad:** el watcher avisa por Telegram al arrancar (👀) y al ejecutar (▶️), además
    de recibida/done/abortada/pausa. Se acabó el "no pasa nada visible".
34. **git no-interactivo:** clone/pull con GIT_TERMINAL_PROMPT=0 + GIT_ASKPASS=echo -> el
    daemon nunca se cuelga pidiendo credenciales; si no hay auth, falla rápido y avisa.

Para automatización total solo falta arrancar el daemon (launchd RunAtLoad+KeepAlive ya
configurado -> persiste y se reinicia solo). Prereq: git auth no-interactiva en el Mac.

---

## Bridge por API de GitHub — sin clon ni auth de git (2026-06-17, run 10)

35. **Modo `REPO_SYNC=github_api`:** el watcher lee `<repo>/missions/inbox` por la API REST de
    GitHub con el PAT (`GITHUB_TOKEN`, contents:read sobre alvaro-pipeline) y descarga los .yaml
    nuevos directo a la inbox. SIN `git clone`, SIN `gh auth login`. Valida igual + avisa Telegram.
    Probado EN VIVO contra el repo real: listó y descargó catering-connect, válida. (El MCP de
    GitHub es de Cowork, no del daemon; por eso el daemon usa la API con token.)
36. `.env`: REPO_SYNC=github_api + GITHUB_REPO + MISSIONS_PATH + GITHUB_TOKEN (el mismo PAT).
    `bridge_check.sh` ahora comprueba la API según el modo. Modo git queda como alternativa.

Resultado: la única acción que te queda es arrancar el daemon (el PAT ya está en .env; sin clon,
sin auth de git).

---

## Conectividad Vercel + Supabase para el agente (2026-06-17, run 11)

37. **Vercel (deploy autónomo):** VERCEL_TOKEN + VERCEL_SCOPE en .env (reusados de empresaAI).
    El agente hereda VERCEL_TOKEN del entorno del daemon -> el CLI de Vercel despliega solo.
    El system_prompt del planner ya le indica cómo (npx vercel --yes --prod --scope=...).
38. **Supabase:** URL + anon key + project_ref en .env (lecturas REST runtime). El MCP oficial
    de gestión (mcp__supabase__*) está cableado en engine._integrations_from_env y se ACTIVA
    automáticamente cuando se rellena SUPABASE_ACCESS_TOKEN (hoy vacío = OFF, seguro).
39. **engine.run_agent(with_integrations=True)**: solo el planner recibe Vercel+Supabase; el
    verificador NO (sigue read-only, aislado). Builder defensivo (pasa mcp_servers solo si el
    SDK lo soporta). Verificado: ClaudeAgentOptions(mcp_servers=...) OK; integraciones se
    construyen bien con/sin token.

⚠️ Pendiente de Álvaro: (a) reiniciar el daemon para cargar el nuevo .env; (b) para gestión
Supabase, un access-token; (c) OJO el proyecto Supabase es COMPARTIDO -> valorar uno dedicado.

---

## Supabase dedicado (2026-06-17, run 12)

40. **Proyecto Supabase DEDICADO** (cuenta shortsinteresante123, ref wxxezbvgoqsxgfpfgefp) en vez
    del compartido -> aísla a AgentOS (riesgo de tocar tablas de otros proyectos resuelto).
    .env: SUPABASE_URL + SUPABASE_PROJECT_REF al proyecto dedicado; anon key vaciada (el agente
    la obtiene por MCP). Falta SUPABASE_ACCESS_TOKEN (lo genera Álvaro en account/tokens) para
    activar el MCP de gestión sobre ese proyecto. Vercel ya cableado (run 11).

41. **Supabase access-token cableado + VERIFICADO.** SUPABASE_ACCESS_TOKEN en .env -> el MCP
    mcp__supabase__* se activa acotado a --project-ref=wxxezbvgoqsxgfpfgefp (el dedicado; NO
    toca otros proyectos de la cuenta). Probado contra la API de gestión: token válido, el
    proyecto dedicado responde ACTIVE_HEALTHY. AgentOS = Vercel + Supabase dedicado completo.
    Falta solo: reiniciar el daemon para cargar el nuevo .env.

---

## Auth headless + métricas live + dashboard mission (2026-06-18, run 13)

42. **Diagnóstico daemon:** macOS TCC bloquea a launchd leer ~/Desktop -> el daemon no podía
    leer .env (err.log: ".env: Operation not permitted"). Fix: deploy_runtime.sh reubica el
    runtime a ~/agentos (fuera de Desktop) y recarga. Además, auth headless del SDK: el daemon
    no alcanza el Keychain de la sesión -> CLAUDE_CODE_OAUTH_TOKEN (sk-ant-oat01, OAuth del PLAN,
    NO api key) en .env.
43. **Métricas en vivo a Supabase:** runner pushea estado/coste en arranque/cierre/pausa, y
    node_bookkeep pushea CADA vuelta (status active, iterations, spend) -> el dashboard ve el
    loop del SDK en tiempo casi real. _RESULT.json gana spend/iterations/result_url/note. Email
    del link al terminar si notify_email. Push resiliente (reintenta sin 'note' si falta la columna).
    Probado: push/read/delete via Data API OK. (Management API de Supabase dando 403 -> token a
    revisar si una misión necesita DDL por MCP; la Data API va bien.)
44. **Misión dashboard:** 2026-06-18-agentos-dashboard — el sistema construye su propio panel
    (lee agentos_missions, explica el loop plan->bookkeep->verify->route, muestra estado/vueltas/
    gasto con auto-refresh, deploy a Vercel, email del link). Cola: confirm-e2e (50, auth check
    barato) -> dashboard (30) -> test-hello (20) -> geo (0).

---

## Blindaje de autonomía (2026-06-20, run 14) — que NADA bloquee el daemon

Tras varias pruebas reales, síntoma: el sistema "no era del todo autónomo". Diagnóstico
con datos reales de Supabase (me los leo yo, sin pedir logs): `confirm-e2e`, `test-hello`
(desplegada sola a Vercel) y `geo` cerraron en VERDE — la auth headless del plan FUNCIONA.
Pero la misión real `aval-framework-spine` (AI Act + sitio público) se quedó atascada y,
como el daemon es SERIE, bloqueó todo lo demás (incluido el dashboard). Dos modos de fallo:
(a) la 1ª llamada al SDK colgada 8,7 h (un comando que no termina: servidor/dev/interactivo);
(b) churn de ~20 vueltas sin cerrar. Fixes:

41. **Timeout por llamada al SDK (engine).** `run_agent` envuelve el consumo del SDK en
    `asyncio.wait_for` (`SDK_CALL_TIMEOUT_SECONDS`, def. 900s=15min). Una llamada colgada ya
    NO bloquea: devuelve is_error -> el loop sigue y el no-progreso/topes la cierran. El
    cuelgue de 8,7 h es imposible por construcción. (test: query que duerme 999s -> se corta.)
42. **Tope de tiempo de pared (bookkeep).** Se sella `started_at` en la 1ª vuelta; bookkeep
    aborta si `now-started > wall_clock_hours`. Antes el budget tenía el campo pero NO se
    aplicaba. (test: misión arrancada hace 2h con tope 1h -> aborta.)
43. **Prompt anti-cuelgue (node_plan).** El agente tiene PROHIBIDO lanzar procesos que no
    terminan (`npm run dev`, `vercel dev`, `http.server`, watchers). Deploys SIEMPRE en modo
    no-interactivo (`--yes`); comandos dudosos envueltos en `timeout`. Verificación con `curl -sf`.
44. **Backstop en el watcher.** Lanza el runner como subproceso vigilado: si lleva
    >`WATCHER_RUNNER_TIMEOUT_SECONDS` (def. 2h) SIN avanzar y NO está esperando un gate
    (marca `_WAITING_GATE` que el runner pone/quita alrededor de `wait_for_decision`), lo
    mata, avisa por Telegram y sigue con la siguiente. Una espera legítima de GO/NO NUNCA se mata.
45. **Auto-recuperación de huérfanas (watcher, al arrancar).** Una misión en active/ sin
    `_PAUSED.json` y sin `_WAITING_GATE` quedó a medias de un reinicio: se abandona limpio +
    avisa + marca 'aborted'. Antes quedaba un fantasma 'active' para siempre. (test: 3 casos.)
46. **Canal `last_cost_usd` + `started_at` declarados en MissionState.** Faltaba declarar
    `last_cost_usd` -> LangGraph podía descartarlo. Ahora el coste real se propaga de plan a
    bookkeep de verdad.
47. **Bug de métricas: el arranque reseteaba el contador.** `metrics.push_mission` hace upsert
    PARCIAL (solo campos con valor); el push de 'active' al (re)arrancar ya NO machaca
    iteraciones/gasto a 0. El dashboard ahora es monótono y fiel. (test contra tabla real.)

Nota: `total_cost_usd` del SDK bajo el plan Max = $0 (no factura por token). El dashboard
mostrará $0 = "incluido en el plan"; el límite real es vueltas + tiempo + no-progreso, no $.
Verificado: smoke TODO VERDE (5) + 4 tests dirigidos (timeout SDK, wall-clock, grafo, estado)
+ 3 tests de auto-recuperación + test de upsert parcial contra Supabase real.

---

## Dashboard v2 — centro de mando con acciones (2026-06-21, run 15)

El dashboard pasa de espejo a CABINA (bundles 1+3+4 del brainstorming). Diseño:
el dashboard es público -> NO escribe directo en tablas (RLS lo bloquea); manda ACCIONES
insertando en `agentos_commands` con contraseña, y una policy `with check (pass=...)` deja
pasar solo si coincide y NO permite leerlas (las consume el daemon con service key).

48. **control.py (NUEVO).** Canal de control dashboard<->daemon vía Supabase:
    - `ensure_schema()`: crea/parcha `agentos_commands`, `agentos_heartbeat` y columnas
      node/last_action/dod/note en agentos_missions (Management API; corre EN EL MAC porque
      Cloudflare bloquea el sandbox). Idempotente -> seguro en cada arranque.
    - `fetch_pending_commands()/finish_command()`: la cola que consume el watcher.
    - `push_heartbeat()`: latido (vivo/idle/running/waiting_gate/frozen + misión activa).
    - `gate_decision()`: GO/NO de un gate llegado desde el dashboard.
    - freeze flag local.
49. **Login por contraseña (Queso2025).** Gate de pantalla completa; compara SHA-256 (el
    plano NO está en el cliente, solo el hash). Tras acertar, la contraseña vive en memoria
    y viaja (HTTPS) en cada inserción de comando, donde RLS la vuelve a validar.
50. **Acciones (bundle 1).** Por misión: Abortar (mata la activa EN CALIENTE desde el
    bucle de vigilancia del watcher), Reintentar (reencola de cero: borra checkpoint+workspace),
    GO/NO de gate. Global: Freeze/Reanudar el daemon. (priority/hold/unhold también los
    ejecuta el daemon; se expondrán en UI cuando haya vista de cola.)
51. **Observabilidad (bundle 3).** El grafo publica cada vuelta `node` (paso actual) y
    `last_action`; verify publica el checklist `dod`. Drill-down al hacer clic en una misión:
    paso actual + última acción + DoD con ✓/✗ + vueltas/gasto. Mini-charts (vueltas y gasto
    por misión).
52. **Sistema (bundle 4).** Barra con latido del daemon (vivo/caído por antigüedad del
    heartbeat), estado, misión activa, nº en rate-limit, y botón Freeze. Filtros por estado
    + búsqueda. Auto-refresh 5 s.
53. **Watcher: consumidor de comandos + heartbeat + freeze + abort en caliente + auto-setup.**
    En arranque: ensure_schema(). En idle: consume comandos + late. En _run: consume comandos
    cada 15 s y mata la misión si llega abort (distinto de timeout). freeze global detiene
    coger/retomar misiones.
54. **gates.wait_for_decision unificado.** Escucha a la vez el dashboard (commands) y
    Telegram/email; lo que llegue primero decide.

Auth: RLS con check de contraseña (sin edge function). El canal de comandos + heartbeat se
crean solos al arrancar el daemon -> se activan con el mismo `deploy_runtime.sh` ya pendiente.
Dashboard desplegado: https://agentos-centro-wcoach24s-projects.vercel.app
Verificado: smoke 5/5 + _apply_command (priority/hold/unhold/abort/retry) + freeze flag +
SCHEMA_SQL + gate GO/NO desde dashboard + JS sin errores (node --check) + deploy HTTP 200.
Pendiente (1 paso, ya previsto): deploy_runtime.sh crea las tablas en el Mac y activa los botones.

---

## Diagnóstico bridge claude.ai + gasto≠stopper + heartbeat (2026-06-21, run 16)

55. **GASTO ya NO es stopper (directiva de Álvaro).** Con el SDK por el plan de
    suscripción, el coste no debe parar nada: prima COMPLETAR con calidad. `node_bookkeep`
    ya no aborta al 80% del crédito; el gasto queda como pura telemetría. Anti-atasco
    siguen: iteraciones, no-progreso, tiempo de pared, timeout SDK. (smoke actualizado.)
56. **heartbeat-file local.** `_touch_heartbeat()` escribe state/watcher_heartbeat.txt cada
    ciclo (idle + durante runs). Liveness comprobable sin depender de Supabase; satisface el
    check 'heartbeat-fresh' de la misión M0 del hilo de claude.ai.

Diagnóstico del "no funciona el hilo de claude.ai" (full breakdown):
- CAUSA RAÍZ: el DAEMON está caído (última actividad Supabase hace ~13,6h, murió tras el
  abort de geo). El plist SÍ tiene KeepAlive=true -> la caída larga = Mac dormido/sesión
  cerrada (los LaunchAgents no corren sin sesión activa). Hay que rearrancarlo una vez.
- El BRIDGE está BIEN: REPO_SYNC=github_api + repo Wcoach24/alvaro-pipeline + token OK; el
  repo tiene las 4 misiones (catering, spcx, aval, M0@47cf670); _sync_github_api correcto;
  aval ya se procesó por esta vía. En cuanto el daemon arranque, baja catering/spcx/M0.
- M0 válida (6 checks máquina + 1 judgment). Matices: (a) heartbeat-file -> añadido; (b)
  repo-public crea Wcoach24/agentos público (necesita gh auth en Mac; es publish autónomo);
  (c) proc watcher.py coincide.
- Bootstrapping: M0 no se recoge a sí misma con el daemon caído -> arranque manual 1 vez.

---

## Daemon revivido: estaba CONGELADO (2026-06-21, run 17)

Síntoma: tras deploy_runtime.sh, "setup esquema falló 403 error 1010" y el hilo de
claude.ai sin procesarse. Diagnóstico real:
57. **Cloudflare bloquea la huella de Python (urllib) en api.supabase.com (403/1010), NO la
    de curl.** Por eso ensure_schema fallaba desde el Mac Y el sandbox. FIX: ensure_schema
    ahora usa `curl` (subprocess) + recarga la caché de PostgREST. Y creé las tablas YA por
    curl desde aquí (commands+heartbeat+columnas). Canal verificado: insert Queso2025->201,
    contraseña mala->401 (RLS protege).
58. **El daemon estaba VIVO pero FROZEN.** El heartbeat (ya con tabla) lo delató: status=
    frozen. Causa: el `_FROZEN` que dejé en state/ de un test se copió a ~/agentos/state/ en
    un deploy_runtime.sh anterior (rsync viejo). Lo descongelé mandando un comando 'unfreeze'
    por el propio canal -> el daemon lo consumió -> empezó a procesar. (deploy_runtime.sh ya
    excluye state/ para que no recurra.)
59. **Re-run limpio (fix).** Re-lanzar una misión ya ejecutada reanudaba su checkpoint viejo
    (started_at antiguo -> abort por wall-clock al instante; p.ej. confirm-e2e it=1). runner
    ahora borra el checkpoint en arranque NO-pausa -> re-runs frescos. (Las misiones nuevas
    de claude.ai no se ven afectadas.)
Confirmado: heartbeat=running, daemon procesando la cola; PAT de GitHub lista las 4 misiones
del repo; M0 válida (6 checks máquina + 1 judgment) -> correrá tras la cola local.

---

## Cola idempotente: cada misión corre UNA vez (2026-06-21, run 18)

Pregunta de Álvaro: "¿por qué se reinician las misiones? ¿no hay cola?". Sí hay cola
(serie+prioridad), pero el inbox del Desktop reañadía misiones YA hechas en cada re-sync
-> el daemon las re-ejecutaba (y abortaban al reanudar checkpoint viejo). Fix:
60. **Registro de idempotencia (state/done_ids.txt).** Al terminar (done/aborted) se marca
    el id. _claim_next_inbox SALTA cualquier yaml cuyo id ya esté hecho (lo saca del inbox a
    _processed sin correr). El retry des-registra (permite re-correr a propósito).
61. **Siembra retroactiva al arrancar.** _seed_ledger_from_history marca lo que ya está en
    _processed/ y done/ -> tras un re-sync, las viejas NO se re-ejecutan ni una vez.
Con el fix nº59 (re-run limpio) esto cierra el "re-arranque": cada misión corre exactamente
una vez; las nuevas de claude.ai corren frescas. Verificado: smoke + test de idempotencia
(ledger, siembra, claim que salta lo hecho, retry que des-registra).

---

## Repo público + acceso GitHub + fix permisos macOS (2026-06-21, run 19)

62. **Repo Wcoach24/agentos creado (público) + README presentable** vía GitHub MCP. Cubre
    el check repo-public de la misión M0 y da la pieza de portfolio (qué/por qué/arquitectura/
    cómo correrlo). El PAT del pipeline NO puede crear repos (scoped a alvaro-pipeline); se usó
    la cuenta GitHub del usuario (Wcoach24) vía MCP.
63. **Fix permisos macOS (TCC) sin congelar.** Los diálogos "Python quiere acceder a carpetas"
    eran el agente de M0 leyendo ~/Desktop (protegido) para publicar AgentOS. node_plan ahora
    instruye al agente: trabajar solo en su workspace y ~/agentos, NUNCA tocar Desktop/Documents/
    Downloads, y publicar desde ~/agentos. Aplica en el próximo re-sync (no ahora: re-sincronizar
    abortaría M0 en curso).
Acceso GitHub del daemon: el agente puede crear/empujar repos con Bash, pero necesita `gh auth
login` en el Mac (o un PAT separado). El PAT del pipeline queda solo para alvaro-pipeline.

---

## Anti-suicidio + diagnóstico M0 (2026-06-21, run 20)

64. **M0 se abortó a sí misma.** Motivo real (columna note): "huérfana tras reinicio del
    daemon". M0 ("estabilizar el watcher") reinició el watcher que la ejecutaba -> _recover_
    orphans la abandonó. FIX system_prompt: el agente tiene PROHIBIDO reiniciar/parar/matar el
    daemon que lo ejecuta (launchctl/kill); si la misión es sobre el watcher, edita ficheros y
    deja nota, el operador recarga aparte.
Pendiente: re-sync (daemon idle -> seguro) aplica idempotencia + checkpoint-limpio + anti-TCC
+ anti-suicidio + curl-schema; luego retry de M0 (ya con gh auth + repo creado) corre limpia.
