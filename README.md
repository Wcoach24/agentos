# AgentOS

**Un agente autónomo que ejecuta misiones hasta un "hecho" verificable por máquina — y se para solo en lo que importa.**

La mayoría de los agentes saben *empezar* tareas. El problema difícil es *pararlas bien*: saber, con evidencia objetiva, que algo está terminado — sin que el propio agente sea juez y parte. AgentOS pone ese juicio en el centro.

---

## Qué es

AgentOS recibe una **misión** (un YAML con objetivo + un *Definition of Done* comprobable) y la lleva sola hasta cerrarla. Cada misión pasa por un bucle:

```
plan  ->  bookkeep  ->  verify  ->  route
```

- **plan** — el agente da el siguiente paso real hacia el objetivo (escribe ficheros, despliega, crea repos…).
- **bookkeep** — lleva la cuenta (coste, vueltas) y aplica topes anti-atasco.
- **verify** — un **verificador independiente** comprueba el *Definition of Done* con **checks de máquina** (no se fía del agente).
- **route** — ¿hecho? cierra. ¿gate? pide aprobación humana. ¿ni una cosa ni otra? otra vuelta, con el feedback del verificador.

## Por qué importa

- **El "hecho" no es falseable.** Toda misión exige al menos un check de **máquina** (`file_exists`, `http_status`, `command_exit_zero`, `file_contains`). El juicio de un LLM (`agent_judgment`) puede *sumar* calidad, pero **nunca cierra una misión por sí solo**.
- **Verificador independiente.** Corre en contexto separado y en modo solo-lectura: no puede tocar los artefactos que juzga (anti reward-hacking).
- **Autónomo por defecto, humano donde toca.** Solo se detiene a pedir **GO/NO** ante **dinero** o algo **irreversible**. Desplegar a una URL pública o crear un repo = autónomo.
- **No se atasca.** Topes de iteraciones, de tiempo, de no-progreso y timeout por llamada al SDK garantizan que ninguna misión bloquee el sistema para siempre.
- **Resumible.** Todo el estado vive en checkpoints; si la máquina se reinicia, retoma donde iba.

## Arquitectura

Cinco capas, desacopladas:

| Capa | Rol |
|---|---|
| **Misión** (YAML) | Objetivo + Definition of Done + presupuesto + política de gates. |
| **Orquestador** (LangGraph) | El grafo de estados `plan -> bookkeep -> verify -> route`, con checkpoints en SQLite. |
| **Motor** (Claude Agent SDK) | Ejecuta cada paso del agente. Corre bajo el plan de suscripción, sin API key. |
| **Verificador** | Comprueba el DoD; mezcla checks de máquina (obligatorios) con juicio de un modelo (opcional, read-only). |
| **Watcher** (daemon) | Vigila la cola de misiones, lanza el runner, retoma pausadas, y atiende un canal de control. |

Los gates humanos (GO/NO) se piden por Telegram o por un dashboard, con consumo idempotente. Las misiones entran por tres puentes: un repo de GitHub (para escribir misiones desde cualquier sitio), local, o un comando directo.

## Cómo correrlo

```bash
# 1. Dependencias
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Prueba de fontanería (offline, sin gastar nada)
python smoke_test.py

# 3. Una misión real (el agente la lleva hasta el DoD)
python -m orchestrator.runner missions/inbox/<tu-mision>.yaml

# 4. Modo daemon: vigila la cola y procesa sola
python bin/watcher.py
```

Una misión es un YAML pequeño:

```yaml
id: 2026-06-21-ejemplo
objective: "Construye y despliega una landing y deja la URL en DEPLOYED_URL.txt"
definition_of_done:
  - id: dod-1
    verify: { type: file_exists, target: "DEPLOYED_URL.txt" }
  - id: dod-2
    verify: { type: command_exit_zero, target: "curl -sf $(cat DEPLOYED_URL.txt)" }
budget: { max_iterations: 20, wall_clock_hours: 4, no_progress_limit: 4 }
gates: { payment: true, irreversible: true }
```

## Estado

Proyecto vivo, en uso real sobre un Mac Mini bajo `launchd`. El núcleo (bucle, verificador no falseable, gates, anti-atasco, resumibilidad) está operativo. Construido sobre el **Claude Agent SDK** y **LangGraph**.

---

*Stack: Python · Claude Agent SDK · LangGraph · SQLite · Supabase (telemetría) · Vercel (deploys).*
