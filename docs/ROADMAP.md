# AgentOS — Roadmap (cómo sacar mejores versiones)

> El motor de mejora del sistema. Revisa este documento cada X tiempo, elige 1–2 ítems por
> impacto/esfuerzo, impleméntalos, deja smoke + e2e en verde, anótalo en `CHANGES.md` y sube
> la versión. Última revisión: 2026-06-17. Versión actual: **v1.0** (e2e real confirmado).

## Cómo versionamos (el bucle de mejora)

1. **Revisar** este roadmap + los "Riesgos abiertos" de abajo.
2. **Elegir** 1–2 ítems (prioriza impacto alto / esfuerzo bajo, y lo que toque el corazón:
   verificador y "parar bien").
3. **Implementar** en una copia, sin romper los invariantes de `ARCHITECTURE.md §9`.
4. **Verificar verde:** `python smoke_test.py` (TODO VERDE) + los e2e con stubs
   (feedback, gate, rate-limit) + `bash confirm.sh` en el Mac (cierre real).
5. **Registrar** en `CHANGES.md` (qué + por qué) y subir la versión aquí.
6. Para validar a fondo: lanzar la idea como una misión real de bajo riesgo y observarla.

Esquema de versión: `vMAJOR.MINOR`. MINOR = mejoras del backlog. MAJOR = cambio de
arquitectura (p.ej. multi-misión, dashboard web, nuevo motor).

## Backlog priorizado

| # | Mejora | Impacto | Esfuerzo | Estado | Notas |
|---|--------|---------|----------|--------|-------|
| 1 | **Gate NATIVO por hook** (`can_use_tool`/PreToolUse): el SDK bloquea dinero/irreversible, no la buena voluntad del agente | Alto | Medio | Pendiente | El campo ya existe en el SDK 0.2.102. Mapear el deny→interrupt de Telegram. |
| 2 | **Métricas en SQLite** (coste/tarea-exitosa, Autonomy Index, DoD pass rate, nº vueltas) | Medio-alto | Bajo | Pendiente | Quick win. Derivar de bookkeep + _RESULT.json. Mostrar en dashboard. |
| 3 | **Check anti-Potemkin** (nuevo verify type `browser_interaction`, Playwright): clic en el CTA y verificar efecto real (request/DOM/DB) | Alto | Medio | Pendiente | HTTP 200 no basta: una URL puede estar viva pero rota. |
| 4 | **Hash de no-progreso incluye el veredicto del verificador** (anti reward-hacking: "borrar el assert" empuja a fallo, no a falso-done) | Alto | Bajo | Pendiente | Tocar `_hash_state` para incluir `verifier_results`. |
| 5 | **Idempotency keys en side-effects** (deploy/email/publicar): exactly-once al reanudar | Alto | Medio | Pendiente | Un resume tras pausa/crash puede re-deployar. Key = hash(misión+paso). |
| 6 | **Evidencia adjunta a cada check** (screenshot/response/exit log) en el gate de Telegram | Medio | Bajo | Pendiente | "Aquí está la prueba" en vez de "confía en el verificador" (estilo Devin). |
| 7 | **Modelo por tarea**: Opus para razonar, Haiku para mecánico; leer `model_usage` | Medio | Bajo | Parcial | Verificador ya es opus. Falta Haiku para pasos mecánicos. |
| 8 | **Tracing** Langfuse self-hosted (callback LangGraph) | Medio | Medio | Pendiente | Gratis, sin per-seat. Trazas de pasos/tools/coste. |
| 9 | **`durability="sync"` explícito** al compilar el grafo | Medio | Bajo | Pendiente | Máxima durabilidad para reanudar tras rate-limit/crash. |
| 10 | **Verificar ToS** del token Max vía SDK (¿permitido fuera de Claude Code?) | Riesgo | Bajo | Pendiente | Hacer antes de dejar el daemon 24/7 a gran escala. |
| 11 | **self-consistency en `agent_judgment`** (varias muestras + mayoría) en vez de un veredicto | Medio | Medio | Idea | El LLM-judge tiene sesgos; varias muestras reducen miscalibración. |
| 12 | **Sandbox nativo del SDK** para el agente ejecutor (campo `sandbox`) | Medio | Medio | Idea | Aislar lo que el agente puede tocar. |

## Apuestas mayores (cambios de arquitectura)

- **Dashboard web con botones GO/NO desde el móvil** (el "v1" que menciona `dashboard.py`).
  Hoy el control humano ya está por Telegram; un panel daría visión + acción en un sitio.
- **Multi-misión en paralelo** (hoy es serie por diseño, para proteger la cuota Max). Solo
  cuando el throughput lo pida y con límites de cuota por misión.
- **Worktrees para variantes** (estilo Cursor): lanzar 2–3 intentos en paralelo y promover el
  que pase el DoD. Útil para tareas con varias soluciones plausibles.
- **Critic execution-free como pre-filtro** (estilo OpenHands): un juez barato puntúa antes de
  gastar checks caros.

## Riesgos abiertos a vigilar

- **ToS del token Max** (ítem 10). El más importante antes de escalar.
- **claude.ai genera un mission.yaml inválido**: mitigado por `validate_mission` en el watcher
  (rechaza + avisa por Telegram), pero conviene afinar el snippet de preferencias con ejemplos.
- **Auth de git en el Mac** para clonar el repo puente privado: si falla, el daemon avisa por
  Telegram y siguen Cowork + Telegram. `bridge_check.sh` lo detecta.
- **Fallback de email (IMAP)**: frágil por diseño (orden/unicidad). Solo se usa si Telegram cae;
  mantener Telegram como principal.

## Histórico de versiones

- **v1.0 (2026-06-17):** e2e real confirmado en el Mac. Verificador estricto + feedback,
  bookkeep con coste real, pausa/resume por rate-limit, gates por Telegram (confirmado en
  vivo), 3 bridges (claude.ai+git / Cowork / Telegram /idea), skills cargadas, validación dura.
  Ver `CHANGES.md` runs 1–7 para el detalle.
