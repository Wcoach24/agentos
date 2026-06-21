"""
graph.py — Orquestador LangGraph.

Grafo de estados:  plan -> bookkeep -> verify -> route
  route:  done?           -> END (cierre + mover a missions/done)
          gate pendiente?  -> interrupt (congela vivo, email, espera GO)
          no-progreso/topes-> END (aborta, avisa)
          si no            -> plan (otra vuelta)

Checkpointing en SQLite (SqliteSaver): cada paso persiste el estado. Si el Mac
se reinicia, se retoma con el mismo thread_id desde el último checkpoint.
Los gates usan interrupt(): el grafo se detiene SIN gastar crédito; al recibir GO
se reanuda con Command(resume=...).

EJECUCIÓN SÍNCRONA (a propósito): el grafo corre con .invoke()/sync SqliteSaver,
no con .ainvoke(). Motivo verificado (jun 2026, langgraph 1.2.5): interrupt() lee
el contexto del runnable vía un contextvar que NO se propaga en ejecución async
bajo Python < 3.11 (get_config -> 'outside of a runnable context'), y el
SqliteSaver síncrono no soporta métodos async (NotImplementedError). La ruta
síncrona funciona en 3.10 y 3.11+. Las llamadas async del SDK/verificador se
puentean con run_sync() dentro de cada nodo (loop efímero por paso).
"""
from __future__ import annotations
import os, json, hashlib, time, asyncio
from typing import TypedDict, Annotated
import operator

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import interrupt, Command

from .engine import run_agent
from .verifier import verify_dod
from . import gates, metrics


def run_sync(coro):
    """Drive an async coroutine to completion from a synchronous graph node.
    Sync .invoke() runs nodes in the main thread with no active event loop, so a
    short-lived asyncio.run() is safe and isolates each SDK/verifier call."""
    return asyncio.run(coro)


class RateLimitPause(Exception):
    """Se topó el límite de uso del plan Max. NO es un fallo de la misión: se pausa
    el grafo (el checkpoint queda intacto) y el watcher la retoma cuando la ventana
    de uso del plan se resetee. Distinto de 'aborted' (que sí cierra la misión)."""


# ---------------------------------------------------------------- estado
class MissionState(TypedDict, total=False):
    mission: dict                       # la misión cargada del yaml
    cwd: str                            # workspace de la misión
    iteration: int
    last_action: str
    log: Annotated[list, operator.add]  # se acumula
    state_hashes: list                  # para detectar no-progreso
    spend_usd: float                    # estimación acumulada
    last_cost_usd: float                # coste real de la última vuelta (lo acumula bookkeep)
    started_at: float                   # epoch del arranque (tope wall_clock_hours)
    verifier_results: list
    done: bool
    aborted: bool
    abort_reason: str
    pending_gate: dict                  # {subject, body} si hay gate
    last_human: str                     # respuesta humana inyectada


def _hash_state(s: MissionState) -> str:
    # Solo last_action: incluir 'iteration' rompía el detector de no-progreso
    # (cambia cada vuelta -> nunca detectaba estancamiento -> loop/coste runaway).
    snap = json.dumps({"last": s.get("last_action")}, sort_keys=True)
    return hashlib.sha256(snap.encode()).hexdigest()[:12]


# ---------------------------------------------------------------- nodos
def node_plan(state: MissionState) -> MissionState:
    m = state["mission"]
    it = state.get("iteration", 0) + 1
    dod_txt = "\n".join(f"- [{d['id']}] {d['check']}" for d in m["definition_of_done"])
    human = state.get("last_human", "")
    # Realimentación del verificador: si la vuelta anterior falló checks, el agente DEBE
    # verlos para autocorregirse. Sin esto, un check estricto haría loop infinito.
    fails = [r for r in state.get("verifier_results", []) if not r.get("passed")]
    verifier_feedback = (
        "RESULTADO DEL VERIFICADOR (vuelta anterior) — el sistema NO cerrará hasta que "
        "estos checks pasen con evidencia objetiva. Corrige EXACTAMENTE esto:\n"
        + "\n".join(f"- {r['id']}: {r['evidence']}" for r in fails) + "\n\n"
    ) if fails else ""
    prompt = (
        f"MISIÓN: {m['objective']}\n\nCONTEXTO: {m.get('context','')}\n\n"
        f"DEFINITION OF DONE (lo que debe ser verdad para terminar):\n{dod_txt}\n\n"
        f"RESTRICCIONES: {'; '.join(m.get('constraints', []))}\n\n"
        + verifier_feedback +
        f"Estás en la vuelta {it}. Tu WORKSPACE es el directorio de trabajo ACTUAL (.). "
        "Lo hecho hasta ahora vive ahí.\n"
        "REGLAS DE WORKSPACE (obligatorias):\n"
        "- Trabaja SOLO dentro de tu directorio actual. NO subas a directorios padre, NO "
        "uses rutas que empiecen por 'missions/', NO crees/muevas/borres nada en "
        "missions/active|done|inbox ni en el resto del repo. Mover tu propio workspace lo ROMPE.\n"
        "- El entregable principal debe llamarse EXACTAMENTE como pida la DoD (p.ej. DOSSIER.md) "
        "y vivir en tu directorio actual. El sistema ya lo moverá a missions/done al cerrar.\n"
        "- Los checks de la DoD que buscan un substring (p.ej. 'Competidores', 'Pricing') son "
        "LITERALES: usa encabezados claros con esas palabras EXACTAS, sin numerar "
        "(escribe '## Competidores', no '## 2. Competidores GEO').\n"
        + (f"INSTRUCCIÓN HUMANA NUEVA: {human}\n" if human else "")
        + "Ejecuta el SIGUIENTE paso concreto hacia la DoD. Crea/edita ficheros reales "
          "en tu directorio actual.\n"
          "GATES — pide aprobación (una línea que empiece por 'GATE:') SOLO en estos casos:\n"
          "- GASTAR DINERO: comprar dominio, API de pago, publicidad, subir de plan.\n"
          "- IRREVERSIBLE-GRAVE: borrar datos, publicar a una audiencia real, o enviar email a terceros.\n"
          "- CAPTCHA / OTP / 2FA: agrúpalos y pídelos.\n"
          "TODO LO DEMÁS ES AUTÓNOMO, incluido desplegar a una URL pública gratis "
          "(p.ej. *.vercel.app) y crear/empujar repos de GitHub (públicos o privados): "
          "NO pidas gate para eso, hazlo.\n"
          "Si no hay gate, actúa y al final resume en una línea que empiece por 'DONE-STEP:'."
    )
    res = run_sync(run_agent(
        prompt,
        system_prompt=(
            "Agente ejecutor autónomo. Acción > narración. Una acción por vuelta.\n"
            "RUTAS (macOS/TCC): trabaja SOLO dentro de tu directorio actual y de ~/agentos. "
            "NUNCA accedas a ~/Desktop, ~/Documents ni ~/Downloads — macOS los bloquea y lanza "
            "diálogos de permisos. El código fuente de AgentOS está en ~/agentos (NO en "
            "~/Desktop). Para publicar el repo, usa el código de ~/agentos.\n"
            "NO TE SUICIDES: NUNCA reinicies, recargues, mates ni pares el daemon/watcher que "
            "te está EJECUTANDO (nada de `launchctl unload/load/kickstart` sobre "
            "com.alvaro.agentos, ni `kill` del proceso watcher/python): te abortarías a ti "
            "mismo a media misión. Si tu objetivo es 'arreglar el watcher', EDITA los ficheros "
            "y deja una nota en el workspace; el operador lo recarga aparte.\n"
            "ANTI-CUELGUE (CRÍTICO): NUNCA ejecutes comandos que no terminen solos — nada de "
            "servidores en primer plano (`npm run dev`, `vercel dev`, `python -m http.server`, "
            "`next dev`), watchers, ni procesos que se quedan a la escucha. Para DESPLEGAR usa "
            "siempre el modo no-interactivo (`npx vercel --yes --prod ...`), nunca el dev server. "
            "Si un comando puede pedir confirmación/input, pásale el flag que lo evita (`--yes`, "
            "`-y`, `--force`) o envuélvelo en `timeout 120 <cmd>`. Para comprobar que un deploy "
            "responde usa `curl -sf` (que termina), no abras un navegador ni un servidor.\n"
            "PUBLICAR: tienes el CLI de Vercel (usa VERCEL_TOKEN del entorno; "
            "`npx vercel --yes --prod --scope=wcoach24s-projects`). "
            "BACKEND: Supabase — herramientas mcp__supabase__* si están disponibles "
            "(crear tablas/edge functions), o REST con la anon key para lecturas."
        ),
        cwd=state["cwd"],
        max_turns=m["budget"].get("max_turns_per_iter", 30),
        setting_sources=["user", "project"],   # descubre las SKILLS del usuario + CLAUDE.md
        skills=m.get("skills", "all"),          # el planner puede autoinvocar tus skills (override por misión)
        with_integrations=True,                 # cablea Vercel (env) + Supabase (MCP) desde el .env
    ))
    if getattr(res, "rate_limited", False):
        # Pausa resumible: no quemamos vueltas ni cerramos la misión.
        raise RateLimitPause(res.error or "límite de uso del plan Max alcanzado")
    text = res.text.strip()
    gate = None
    for line in text.splitlines():
        if line.strip().upper().startswith("GATE:"):
            gate = {"subject": line.split(":", 1)[1].strip()[:70], "body": text[:1500]}
            break
    err_note = " [SDK ERROR]" if getattr(res, "is_error", False) else ""
    out: MissionState = {
        "iteration": it,
        "last_action": (text[:300] or f"(sin salida){err_note}"),
        "log": [f"[plan/act it{it}]{err_note} {text[:200]}"],
        "last_cost_usd": getattr(res, "cost_usd", 0.0),  # coste REAL del SDK; lo acumula node_bookkeep
        "last_human": "",  # consumida
    }
    if gate:
        out["pending_gate"] = gate
    if not state.get("started_at"):
        out["started_at"] = time.time()  # marca de tiempo para el tope wall_clock_hours
    return out


def node_bookkeep(state: MissionState) -> MissionState:
    """Contabilidad por vuelta (entre plan y verify). Acumula el coste REAL del SDK
    (ResultMessage.total_cost_usd, no una estimación), registra el hash para el
    detector de no-progreso, y aplica los TOPES: max_iterations, 80% del crédito y
    no-progreso. Si alguno salta, marca aborted+motivo. Separa 'llevar la cuenta' del
    planificar (plan) y del juzgar (verify); así route() queda como decisión pura."""
    b = state["mission"]["budget"]
    spend = state.get("spend_usd", 0.0) + state.get("last_cost_usd", 0.0)
    out: MissionState = {
        "spend_usd": spend,
        "last_cost_usd": 0.0,
        "log": [f"[bookkeep] it={state.get('iteration')} gasto_real=${spend:.4f}/{b['credit_usd']}"],
    }
    # SDK en VIVO: publica el estado de esta vuelta a Supabase (para el dashboard en tiempo real)
    try:
        _m = state["mission"]
        metrics.push_mission(_m["id"], _m.get("title", ""), "active", False,
                             spend, state.get("iteration", 0),
                             node="plan", last_action=state.get("last_action", ""))
    except Exception:
        pass
    if state.get("iteration", 0) >= b["max_iterations"]:
        out["aborted"] = True; out["abort_reason"] = "Máximo de iteraciones."
        return out
    started = state.get("started_at", 0)
    wall_h = b.get("wall_clock_hours", 0)
    if started and wall_h and (time.time() - started) > wall_h * 3600:
        out["aborted"] = True
        out["abort_reason"] = f"Límite de tiempo de pared ({wall_h}h) superado."
        return out
    # GASTO = SOLO TELEMETRÍA (no es stopper). Con el SDK por el plan de suscripción de
    # Claude, el coste no debe parar nada: prima que la misión se COMPLETE con calidad.
    # Los anti-atasco siguen siendo iteraciones / no-progreso / tiempo de pared / timeout SDK.
    limit = b.get("no_progress_limit", 4)
    hashes = (state.get("state_hashes", []) + [_hash_state(state)])[-limit:]
    out["state_hashes"] = hashes
    if len(hashes) == limit and len(set(hashes)) == 1:
        out["aborted"] = True; out["abort_reason"] = "Sin progreso en K vueltas."
    return out


def node_verify(state: MissionState) -> MissionState:
    m = state["mission"]
    if state.get("pending_gate") or state.get("aborted"):
        return {"log": ["[verify] saltado (gate o abort pendiente; no se gasta SDK)"]}
    all_ok, results = run_sync(verify_dod(m["definition_of_done"], state["cwd"], m["objective"]))
    dod = [{"id": r.dod_id, "passed": r.passed, "evidence": r.evidence} for r in results]
    # publica el checklist del DoD para el drill-down del dashboard
    try:
        metrics.push_mission(m["id"], m.get("title", ""), "active", False,
                             node="verify", dod=[{**d, "evidence": (d["evidence"] or "")[:200]} for d in dod])
    except Exception:
        pass
    return {
        "verifier_results": dod,
        "done": all_ok,
        "log": [f"[verify] done={all_ok} :: " + "; ".join(f"{r.dod_id}={r.passed}" for r in results)],
    }


def node_gate_notify(state: MissionState) -> MissionState:
    """Envía el email [GATE] UNA sola vez. Va en un nodo separado del wait a
    propósito: en langgraph el nodo que llama interrupt() se re-ejecuta ENTERO al
    reanudar, así que cualquier efecto secundario (mandar el correo) debe vivir en
    un nodo previo ya 'checkpointed' para no duplicar la notificación."""
    g = state["pending_gate"]
    m = state["mission"]
    to = m.get("notify_email") or os.environ.get("DISPATCHER_EMAIL", "")
    gates.send_gate(m["id"], g["subject"], g["body"], to=to or None, decision=True)
    return {"log": [f"[gate] [GATE] enviado (con botones GO/NO) -> {g['subject']}"]}


def node_gate_wait(state: MissionState) -> MissionState:
    """Congela el grafo y espera la decisión humana. interrupt() pausa SIN gastar
    crédito; al reanudar con Command(resume={...}) llega 'decision'. Este nodo se
    re-ejecuta al reanudar (por eso el envío del correo está en node_gate_notify)."""
    g = state["pending_gate"]
    m = state["mission"]
    decision = interrupt({"type": "payment_gate", "mission": m["id"], "subject": g["subject"]})
    approved = bool(decision.get("approved"))
    human = decision.get("instructions", "")
    if not approved:
        return {"aborted": True, "abort_reason": f"Gate rechazado: {g['subject']}",
                "log": [f"[gate] NO -> abortado"], "pending_gate": {}}
    return {"pending_gate": {}, "last_human": human or "Gate aprobado, continúa.",
            "log": [f"[gate] GO -> {g['subject']}"]}


def route(state: MissionState) -> str:
    """Decisión PURA (sin efectos secundarios). Los topes y la contabilidad viven en
    node_bookkeep; aquí solo se elige la siguiente arista."""
    if state.get("aborted"):
        return "end"
    if state.get("done"):
        return "end"
    if state.get("pending_gate"):
        return "gate"
    return "plan"


# ---------------------------------------------------------------- build
def build_graph(checkpoint_path: str):
    g = StateGraph(MissionState)
    g.add_node("plan", node_plan)
    g.add_node("bookkeep", node_bookkeep)
    g.add_node("verify", node_verify)
    g.add_node("gate_notify", node_gate_notify)
    g.add_node("gate_wait", node_gate_wait)
    g.add_edge(START, "plan")
    g.add_edge("plan", "bookkeep")
    g.add_edge("bookkeep", "verify")
    g.add_conditional_edges("verify", route, {"plan": "plan", "gate": "gate_notify", "end": END})
    g.add_edge("gate_notify", "gate_wait")
    g.add_edge("gate_wait", "plan")
    saver = SqliteSaver.from_conn_string(checkpoint_path)
    return g, saver
