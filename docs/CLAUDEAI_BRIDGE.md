# Bridge claude.ai → Mac (git) — tu idea original

El flujo que quieres: trabajas una idea en **claude.ai** (web), dices **"continúalo solo"**,
claude.ai (con su conector de GitHub, igual que ya lo tienes) **commitea el `mission.yaml`**
a un repo, el Mac hace **`git pull`**, lo **valida**, y el **SDK** lo ejecuta solo. Los gates
vuelven a tu Telegram.

```
claude.ai (chat + conector GitHub)
      │  "continúalo solo"  → genera mission.yaml → commit a repo/missions/inbox/<id>.yaml
      ▼
GitHub repo (puente)
      │  el watcher del Mac hace git pull cada ciclo
      ▼
Mac: watcher → valida (≥1 check de máquina) → inbox local → runner → SDK (autónomo)
      │  gates / done / abortada → Telegram
      ▼
done/ + aviso en Telegram
```

El SDK NO se conecta a claude.ai: lee el `mission.yaml`. claude.ai solo PRODUCE ese yaml.
La validación del watcher es la red de seguridad: si claude.ai genera una DoD sin check de
máquina, se RECHAZA y te avisa por Telegram (no se ejecuta basura).

## Setup (una vez)

1. **Repo puente.** Reusa `Wcoach24/alvaro-pipeline` (añade la carpeta `missions/inbox/`)
   o crea uno dedicado `Wcoach24/agent-os-missions` (privado). Solo necesita la ruta
   `missions/inbox/`.

2. **Clónalo en el Mac** (donde el watcher hará pull):
   ```bash
   git clone https://github.com/Wcoach24/alvaro-pipeline ~/agent-os-missions
   ```

3. **Activa el modo git en `.env`:**
   ```
   REPO_SYNC=git
   REPO_DIR=/Users/zorro/agent-os-missions
   ```
   (El modo `local` de Cowork sigue funcionando en paralelo: ambos alimentan la misma inbox.)

4. **Conector de GitHub en claude.ai:** ya lo tienes. Asegúrate de que tiene acceso al repo puente.

5. **Pega esto en las preferencias de claude.ai** (igual que el pipeline de artefactos):

```
## AgentOS — handoff "continúalo solo"
Tengo un sistema autónomo en mi Mac que ejecuta misiones desde un repo GitHub.
Repo puente: Wcoach24/alvaro-pipeline, carpeta missions/inbox/.

Cuando diga "continúalo solo" (o equivalente), destila esta conversación en un
mission.yaml y, vía el conector de GitHub, haz commit a missions/inbox/<id>.yaml en main.
Enséñame el yaml y confirma el commit.

El mission.yaml DEBE cumplir:
- id: slug con fecha (ej 2026-06-17-landing-esgeo); title (<=80); objective (1-2 frases);
  context (lo destilado del hilo); done_level: staging|production.
- definition_of_done: lista de criterios VERIFICABLES con evidencia objetiva. OBLIGATORIO
  >=1 check de MÁQUINA: file_exists | http_status | command_exit_zero | file_contains.
  Cada item: {id, check, verify:{type, target, expected?, rubric?}}. Los target de fichero
  son relativos al workspace. agent_judgment puede SUMAR calidad pero NUNCA cierra solo.
- budget: {max_iterations: 20, credit_usd: 5.0, no_progress_limit: 4, wall_clock_hours: 6}.
- gates: {payment: true, irreversible: true}.
Regla de oro: el "done" debe poder comprobarse con una máquina, no con un juicio. Si no es
verificable objetivamente, no es una buena misión: re-formula el DoD hasta que lo sea.
```

## Dos caminos, una inbox

| Origen | Cómo entra | Quién destila la idea | Mejor para |
|--------|-----------|----------------------|------------|
| **claude.ai (web) + git** | commit → `git pull` | claude.ai (contexto rico del hilo) | ideas trabajadas a fondo |
| **Cowork** | escribe directo en inbox | Claude en Cowork | cuando trabajas conmigo aquí |
| **Telegram `/idea`** | el Mac destila + encola | el SDK del Mac (dispatcher) | rápido, desde el móvil |

Los tres terminan en `missions/inbox/`, se validan igual, y el runner los ejecuta igual.
