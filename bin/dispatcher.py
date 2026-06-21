#!/usr/bin/env python3
"""
dispatcher.py — El cerebro del BRIDGE "desde cualquier sitio".

Toma una IDEA en lenguaje natural y la DESTILA (con el SDK) en un mission.yaml con
DoD verificable, lo valida y lo encola en missions/inbox/. Así no hace falta que
claude.ai "se conecte" al Mac: tú mandas la idea (Telegram /idea, o CLI), y el Mac
—que SÍ tiene el plan Max— la convierte en misión y la arranca.

Uso CLI:  python bin/dispatcher.py "construye una landing para X que capture emails"
"""
from __future__ import annotations
import sys, os, re, asyncio, datetime
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from orchestrator.engine import run_agent          # noqa: E402
from orchestrator.runner import validate_mission, ROOT  # noqa: E402

INBOX = os.path.join(ROOT, "missions", "inbox")

_SCHEMA_HINT = """Convierte la IDEA del usuario en un mission.yaml para un agente autónomo.
Reglas DURAS:
- Devuelve SOLO un bloque YAML válido, nada de prosa fuera.
- Campos: id, title(<=80), objective(1-2 frases), context, done_level(staging|production),
  definition_of_done (lista), constraints (lista), budget, gates.
- definition_of_done: cada item {id, check, verify:{type,target,expected?,rubric?}}.
  OBLIGATORIO >=1 check de MÁQUINA: file_exists | http_status | command_exit_zero | file_contains.
  Los target de fichero son RELATIVOS al workspace (p.ej. "index.html", no rutas absolutas).
  agent_judgment puede SUMAR calidad pero NO puede ser el único check.
- budget: {max_iterations: 20, credit_usd: 5.0, no_progress_limit: 4, wall_clock_hours: 6} (ajusta si procede).
- gates: {payment: true, irreversible: true}.
- Pon id="PLACEHOLDER" (lo fijo yo).
La DoD debe ser VERIFICABLE con evidencia objetiva: el sistema solo cierra cuando esos checks pasan."""


def _slug(text: str, maxlen: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s[:maxlen].rstrip("-")) or "mision"


def _extract_yaml(text: str) -> str:
    m = re.search(r"```(?:yaml)?\s*(.*?)```", text, re.DOTALL)
    return (m.group(1) if m else text).strip()


async def distill_idea(idea: str, mission_id: str) -> dict:
    """Usa el SDK para convertir la idea en un dict de misión validado."""
    prompt = f"{_SCHEMA_HINT}\n\nIDEA DEL USUARIO:\n{idea}\n\nDevuelve SOLO el bloque YAML."
    res = await run_agent(
        prompt,
        system_prompt="Eres el dispatcher de AgentOS. Destilas ideas en mission.yaml VERIFICABLES.",
        allowed_tools=[], max_turns=1, skills=[],
    )
    m = yaml.safe_load(_extract_yaml(res.text))
    if not isinstance(m, dict):
        raise ValueError("el dispatcher no devolvió un YAML de misión válido")
    m["id"] = mission_id
    m.setdefault("priority", 0)
    validate_mission(m)          # mismo contrato que el runner: rechaza DoD sin check de máquina
    return m


def write_inbox(mission: dict) -> str:
    os.makedirs(INBOX, exist_ok=True)
    path = os.path.join(INBOX, f"{mission['id']}.yaml")
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(mission, f, allow_unicode=True, sort_keys=False)
    return path


def handle_idea(idea: str) -> str:
    """Destila + valida + encola. Devuelve un texto de confirmación (para Telegram)."""
    mid = f"{datetime.date.today().isoformat()}-{_slug(idea)}"
    m = asyncio.run(distill_idea(idea, mid))
    write_inbox(m)
    dod = m["definition_of_done"]
    return (f"✅ Misión encolada: {mid}\n"
            f"🎯 {m['objective'][:240]}\n"
            f"✔️ DoD: {len(dod)} criterios (≥1 de máquina) — el sistema solo cerrará cuando pasen.")


def main():
    idea = " ".join(sys.argv[1:]).strip()
    if not idea:
        print('uso: python bin/dispatcher.py "tu idea en una frase"')
        sys.exit(1)
    try:
        print(handle_idea(idea))
    except Exception as e:
        print(f"❌ No pude destilar la idea en una misión válida: {e}")
        sys.exit(2)


if __name__ == "__main__":
    main()
