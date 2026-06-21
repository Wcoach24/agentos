# El Bridge — de un chat al Mac Mini

Cómo una idea cruza desde donde la piensas (un chat) hasta el sistema autónomo del Mac.

## Los 3 sitios

1. **Claude.ai / Cowork (origen).** Trabajas una idea. Cuando está madura dices
   **"continúalo solo"**. Claude la destila en un `mission.yaml` con DoD verificable.
2. **El inbox (`missions/inbox/`) (puente).** El yaml validado aterriza aquí.
3. **El Mac Mini (cerebro).** El watcher recoge el yaml, arranca el runner, corre el
   loop hasta el `done` verificado, y te avisa por email solo de done / gate / abortada.

## Modo por defecto: LOCAL (recomendado)

`.env`: `REPO_SYNC=local`

En **Cowork** (este chat tiene acceso a tu carpeta), al decir "continúalo solo":
1. Claude genera el dict de misión.
2. Lo valida + escribe en el inbox con el helper:

```bash
python bin/new_mission.py /ruta/a/mission.yaml
# -> [bridge] misión validada y encolada en inbox: .../missions/inbox/<id>.yaml
```

O por código: `from bin.new_mission import write_mission; write_mission(mission_dict)`.

3. El watcher (daemon) la arranca. Si no hay daemon todavía, a mano:
   `bash run.sh`  ó  `python -m orchestrator.runner missions/inbox/<id>.yaml`.

La validación rechaza cualquier misión cuya DoD no tenga ≥1 check de máquina
(file_exists / http_status / command_exit_zero / file_contains). El `agent_judgment`
nunca cierra solo. Esto importa porque el handoff es **auto-go** (no revisas el yaml):
el contrato lo blinda el sistema, no tu ojo.

## Modo opcional: GIT (para chats de Claude.ai SIN acceso al Mac)

`.env`: `REPO_SYNC=git` y `REPO_DIR=/ruta/al/clon`

- Claude commitea el `mission.yaml` a `missions/inbox/` del repo puente.
- El watcher hace `git pull` cada ciclo y copia los yaml nuevos al inbox local.
- Usa credenciales de **solo-lectura propias** para el pull; **NO** el token del
  pipeline de artefactos (scope `artifacts/`).

## Anatomía mínima de un mission.yaml

```yaml
id: 2026-06-17-landing-esgeo        # slug único con fecha
title: "Landing esGEO v2"            # <=80 chars
objective: "Una landing viva que explique esGEO y capture emails."
context: "Lo destilado del hilo: qué es, tono, referencias."
done_level: staging                  # staging (URL gratis) | production (dominio, gates)
priority: 0                          # mayor salta la cola
definition_of_done:
  - id: dod-1                        # >=1 check de MÁQUINA obligatorio
    check: "La landing responde 200 en su URL pública"
    verify: { type: http_status, target: "https://x.vercel.app", expected: "200" }
  - id: dod-2
    check: "El form de email existe y envía"
    verify: { type: agent_judgment, rubric: "El form captura email y confirma envío." }
budget: { max_iterations: 20, credit_usd: 5.0, no_progress_limit: 4, wall_clock_hours: 6 }
gates: { payment: true, irreversible: true }
# skills: all                        # opcional: restringe a ["gsd-pro","impeccable",...]
```
