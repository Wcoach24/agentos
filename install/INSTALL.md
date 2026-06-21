# AgentOS — Instalación por sitios

Sistema de agentes autónomos: empiezas un hilo en Claude.ai, dices "continúalo solo",
y una misión corre en bucle en tu Mac Mini hasta cumplir su Definition-of-Done,
consultándote SOLO los pagos. Motor: Claude Agent SDK bajo tu plan Max 20x (crédito
capado, sin pago-por-token). Orquestación: LangGraph con checkpointing SQLite.

Hay TRES sitios. Cada uno tiene su parte.

---

## SITIO 1 — El Mac Mini (el cerebro, corre 24/7)

Aquí vive todo el sistema. Pasos:

```bash
# 1. Coloca el paquete
mkdir -p ~/agent-os && cd ~/agent-os
#    (copia aquí el contenido de este zip: orchestrator/, bin/, schemas/, etc.)

# 2. Entorno Python (3.10+)
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. Login del plan Max 20x (UNA vez, interactivo). Esto autentica el SDK
#    contra tu suscripción, no contra una API key.
claude                 # abre OAuth en el navegador, completa login, cierra
claude /status         # confirma que la ruta activa es tu plan (no API key)

# 4. (ACTUALIZADO 2026-06-16) NO hay crédito que reclamar.
#    El cambio del 15-jun (pool de crédito Agent SDK aparte, ~200$/mes) se PAUSÓ.
#    Ahora el Agent SDK consume de los límites de tu suscripción Max, igual que el
#    Claude Code interactivo: cuota compartida, sin pago-por-token. No toques nada aquí.
#    OJO: como el SDK come de tu cuota Max, una misión desbocada puede dejarte sin
#    Claude interactivo -> por eso hay tope de consumo por misión (budget.credit_usd,
#    autopausa al 80%) y, si topas el límite del plan, la misión se PAUSA y el watcher
#    la retoma sola cuando la ventana de uso se resetea (no pierdes progreso).

# 5. Secretos
cp .env.example .env
#    rellena SMTP_USER, SMTP_PASS (App Password de Gmail, no la real), DISPATCHER_EMAIL.
#    NO pongas ANTHROPIC_API_KEY. (El sistema la borra igualmente por seguridad.)

# 6. Prueba la fontanería SIN gastar crédito
python smoke_test.py        # debe salir "TODO VERDE"

# 7. Primera misión real de prueba (reversible, sin dinero):
#    ya viene en missions/inbox/2026-06-16-geo-es-dossier.yaml
python -m orchestrator.runner missions/inbox/2026-06-16-geo-es-dossier.yaml
#    míralo correr. Cuando termine, el dossier está en missions/done/.../DOSSIER.md
#    y te llega un email [GATE] ... COMPLETADA.

# 8. Cuando confíes, déjalo de daemon (vigila la inbox y lanza solo):
#    edita las rutas <<<EDITA>>> de install/com.alvaro.agentos.watcher.plist
cp install/com.alvaro.agentos.watcher.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.alvaro.agentos.watcher.plist
launchctl start com.alvaro.agentos.watcher
#    logs: tail -f ~/agent-os/state/watcher.*.log
```

---

## SITIO 2 — El repo `alvaro-pipeline` (el puente de handoff)

Cuando en un chat digas "continúalo solo", yo (Claude) genero el `mission.yaml`.
El repo es donde aterriza para cruzar a tu Mac Mini.

**Estructura a añadir en el repo** (fuera de `artifacts/`, por eso te pido permiso
explícito antes de que YO toque nada ahí — tu regla del pipeline):

```
alvaro-pipeline/
  missions/
    inbox/        <- los mission.yaml nuevos caen aquí
    _processed/   <- el watcher los mueve aquí al terminar
```

**Cómo cruza al Mac Mini** — dos opciones (elige en `.env`, `REPO_SYNC`):

- `REPO_SYNC=local` (recomendado para empezar): tú o Cowork dejáis el yaml en
  `~/agent-os/missions/inbox/` directamente. Cero acoplamiento con git.
- `REPO_SYNC=git` + `REPO_DIR=/ruta/al/clon`: el watcher hace `git pull` cada
  ciclo y copia los yaml del repo a la inbox local. Versionado y auditable.

> Nota: con `REPO_SYNC=git` necesitas un clon del repo en el Mac Mini con permiso
> de pull. NO uses el token del pipeline (scope artifacts/) para esto: o clonas con
> credenciales de solo-lectura propias, o usas el modo `local`.

---

## SITIO 3 — Claude.ai (este chat, el origen)

No instalas nada aquí. El flujo es:

1. Trabajamos una idea en un hilo normal.
2. Cuando esté lista para autonomía, dices: **"continúalo solo"** (o similar).
3. Yo destilo el hilo en un `mission.yaml` con DoD verificable y te lo enseño.
4. Lo dejas en `missions/inbox/` (subiéndolo al repo, o pegándolo en el Mac Mini).
5. El watcher lo recoge y arranca. Tú solo respondes los emails `[GATE]`.

---

## Qué decides tú vs. qué hace solo

| Acción                                   | ¿Autónomo? |
|------------------------------------------|------------|
| Leer, buscar, analizar                   | Sí         |
| Escribir/editar ficheros, codear         | Sí         |
| Deploy a staging, cuentas gratuitas      | Sí         |
| **Comprar dominio**                      | GATE → tú  |
| **Invertir en publicidad**               | GATE → tú  |
| **Upgrade a plan de pago**               | GATE → tú  |
| **Publicar / enviar email externo**      | GATE → tú  |
| Captcha / OTP / 2FA                       | Lote → tú  |

---

## Los límites reales (y sus workarounds, ya implementados)

1. **Captcha/OTP no se automatizan** (anti-bot por diseño). → El agente agrupa los
   puntos humanos y te los pide en lote por email, no de uno en uno.
2. **Crédito finito (~200$/mes Max 20x).** → Tope por misión en el yaml (`credit_usd`),
   autopausa al 80%, y techo duro del plan si agotas el mes (con usage credits off).
3. **Contexto largo.** → El estado vive en SQLite + ficheros del workspace; cada
   vuelta del loop arranca casi limpia leyendo el estado. Loop largo, iteración corta.
4. **Auth desatendida.** → El daemon arranca SIN `ANTHROPIC_API_KEY` para forzar el
   plan. El CLI refresca el token OAuth. Si caduca, `claude` interactivo lo renueva.
5. **El loop miente/se atasca.** → Verificador independiente (evidencia objetiva) +
   detector de no-progreso (para si el estado no cambia en K vueltas).

---

## Mapa de ficheros

```
agent-os/
  orchestrator/
    engine.py      motor sobre Claude Agent SDK (plan, no API key)
    graph.py       grafo LangGraph: plan->act->verify->route + gates + checkpoint
    runner.py      ejecuta una misión, gestiona interrupt/resume
    verifier.py    comprueba la DoD con evidencia objetiva
    gates.py       email SMTP/IMAP: pide GO/NO, espera sin gastar crédito
  bin/
    watcher.py     vigila la inbox y lanza misiones (el puente)
  install/
    com.alvaro.agentos.watcher.plist   daemon launchd
  schemas/
    mission.schema.json
  missions/
    inbox/         misiones por arrancar
    active/        en curso
    done/          terminadas (resultados aquí)
  state/           checkpoints.sqlite + logs
  requirements.txt
  .env.example
  smoke_test.py
```
