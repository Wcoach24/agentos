"""
runner.py — Ejecuta una misión a través del grafo, de principio a 'done'.

Maneja el ciclo de interrupt/resume: cuando el grafo se congela en un gate,
el runner espera la decisión humana (IMAP) y reanuda con Command(resume=...).
Todo el estado vive en el checkpointer SQLite -> resumible tras crash.

Uso:
    python -m orchestrator.runner missions/active/<id>.yaml
"""
from __future__ import annotations
import sys, os, shutil, json, time, sqlite3
import yaml

from .graph import build_graph, MissionState, RateLimitPause
from . import gates, metrics

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.path.join(ROOT, "state")
CHECKPOINT_DB = os.path.join(STATE_DIR, "checkpoints.sqlite")

# 'Done' no falseable: toda misión exige >=1 check de MÁQUINA (no agent_judgment).
OBJECTIVE_CHECKS = {"file_exists", "http_status", "command_exit_zero", "file_contains"}


def validate_mission(m: dict) -> None:
    """Guardarraíl del verificador (el corazón). Como el handoff es auto-go (no revisas
    el yaml), validamos aquí que el 'done' no puede cerrarse solo con el juicio de un LLM."""
    for field in ("id", "title", "objective", "definition_of_done", "budget", "gates"):
        if not m.get(field):
            raise ValueError(f"mission inválida: falta el campo obligatorio '{field}'")
    dod = m["definition_of_done"]
    kinds = {d.get("verify", {}).get("type") for d in dod}
    if not (kinds & OBJECTIVE_CHECKS):
        raise ValueError(
            "mission inválida: la DoD necesita AL MENOS un check de máquina "
            f"{sorted(OBJECTIVE_CHECKS)}; agent_judgment no puede cerrar una misión solo.")


def load_mission(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        m = yaml.safe_load(f)
    validate_mission(m)
    return m


def mission_workspace(mission_id: str) -> str:
    ws = os.path.join(ROOT, "missions", "active", mission_id)
    os.makedirs(ws, exist_ok=True)
    return ws


def run_mission(path: str) -> None:
    m = load_mission(path)
    mid = m["id"]
    ws = mission_workspace(mid)
    os.makedirs(STATE_DIR, exist_ok=True)
    # ¿reanudación por rate-limit (hay _PAUSED) o arranque fresco? Si es fresco, borra
    # cualquier checkpoint viejo del mismo id para no reanudar estado obsoleto (re-run limpio).
    resuming = os.path.isfile(_paused_marker(ws))
    _clear_paused(ws)
    if not resuming:
        _clear_checkpoint(mid)
    metrics.push_mission(mid, m.get("title", ""), "active", False)

    graph_def, saver = build_graph(CHECKPOINT_DB)
    with saver as checkpointer:
        app = graph_def.compile(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": mid}}

        init: MissionState = {
            "mission": m, "cwd": ws, "iteration": 0,
            "log": [], "state_hashes": [], "spend_usd": 0.0, "done": False, "aborted": False,
        }

        # Primer arranque o reanudación: si ya hay checkpoint, invoke sin init lo retoma.
        try:
            result = _drive(app, init, config, mid)
        except RateLimitPause as e:
            # Pausa resumible: NO se finaliza, el checkpoint queda intacto.
            _mark_paused(ws, str(e))
            metrics.push_mission(mid, m.get("title", ""), "paused", False, note="rate-limit del plan")
            print(f"[runner] MISIÓN PAUSADA: {mid} — límite de uso del plan")
            gates.send_gate(mid, "PAUSADA",
                            f"La misión '{m['title']}' se pausó por el límite de uso de tu plan Max "
                            f"({e}). El checkpoint queda intacto y no has perdido progreso; el watcher "
                            f"la retoma sola cuando la ventana de uso se resetee.")
            sys.exit(75)  # EX_TEMPFAIL: el watcher lo interpreta como 'reintentar luego'

        # Cierre
        spend = float(result.get("spend_usd", 0.0) or 0.0)
        iters = int(result.get("iteration", 0) or 0)
        url = _result_url(ws)
        if result.get("done"):
            _finalize(m, ws, ok=True, spend_usd=spend, iterations=iters, result_url=url)
            metrics.push_mission(mid, m.get("title", ""), "done", True, spend, iters, url)
            print(f"[runner] MISIÓN COMPLETADA: {mid}")
            body = f"La misión '{m['title']}' pasó el verificador." + (f"\n\n🔗 {url}" if url else f"\nResultado en missions/done/{mid}/.")
            gates.send_gate(mid, "COMPLETADA", body)
            to = m.get("notify_email")
            if to and url:
                try:
                    gates._email_send_gate(mid, f"COMPLETADA — {url}", body, to=to)
                except Exception as e:
                    print(f"[runner] email notify falló: {e}")
        else:
            reason = result.get("abort_reason", "desconocido")
            _finalize(m, ws, ok=False, spend_usd=spend, iterations=iters, result_url=url, note=reason)
            metrics.push_mission(mid, m.get("title", ""), "aborted", False, spend, iters, url, note=reason)
            print(f"[runner] MISIÓN DETENIDA: {mid} — {reason}")
            gates.send_gate(mid, "DETENIDA", f"La misión '{m['title']}' se detuvo: {reason}")


def _drive(app, init, config, mid) -> dict:
    """Avanza el grafo; si se interrumpe en un gate, espera humano y reanuda."""
    state = app.invoke(init, config=config)
    while True:
        snapshot = app.get_state(config)
        if not snapshot.next:           # no hay nodo pendiente -> terminó
            return state
        # Hay interrupt pendiente (gate). Espera decisión humana por email/Telegram.
        # Marca 'esperando gate' para que el watcher NO confunda esta espera legítima
        # (puede durar horas) con un cuelgue y la mate.
        ws = os.path.join(ROOT, "missions", "active", mid)
        _mark_gate_waiting(ws, True)
        print(f"[runner] gate pendiente en {mid}; esperando GO/NO...")
        try:
            decision = gates.wait_for_decision(mid)
        finally:
            _mark_gate_waiting(ws, False)
        cmd_payload = {"approved": decision.approved, "instructions": decision.raw_reply}
        from langgraph.types import Command
        state = app.invoke(Command(resume=cmd_payload), config=config)


def _gate_waiting_marker(ws: str) -> str:
    return os.path.join(ws, "_WAITING_GATE")


def _mark_gate_waiting(ws: str, on: bool) -> None:
    """Señala al watcher que esta misión está bloqueada esperando una decisión humana
    (gate), no colgada. Mientras exista la marca, el watcher no aplica su timeout."""
    try:
        p = _gate_waiting_marker(ws)
        if on:
            os.makedirs(ws, exist_ok=True)
            open(p, "w").write(str(time.time()))
        elif os.path.isfile(p):
            os.remove(p)
    except Exception:
        pass


def _clear_checkpoint(mid: str) -> None:
    """Borra el checkpoint del thread para un arranque FRESCO. Sin esto, re-lanzar una
    misión ya ejecutada reanuda su estado viejo (started_at antiguo -> aborta por tope de
    tiempo al instante; iteración heredada). Solo se llama en arranque NO-pausa."""
    try:
        con = sqlite3.connect(CHECKPOINT_DB)
        for t in ("writes", "checkpoint_blobs", "checkpoints"):
            try: con.execute(f"DELETE FROM {t} WHERE thread_id=?", (mid,))
            except Exception: pass
        con.commit(); con.close()
    except Exception:
        pass


def _paused_marker(ws: str) -> str:
    return os.path.join(ws, "_PAUSED.json")


def _mark_paused(ws: str, reason: str) -> None:
    try:
        os.makedirs(ws, exist_ok=True)
        with open(_paused_marker(ws), "w", encoding="utf-8") as f:
            json.dump({"paused": True, "reason": reason[:300], "ts": time.time()}, f, ensure_ascii=False)
    except Exception:
        pass


def _clear_paused(ws: str) -> None:
    try:
        p = _paused_marker(ws)
        if os.path.isfile(p):
            os.remove(p)
    except Exception:
        pass


def _result_url(ws: str):
    """Lee la URL del deploy si el agente la dejó en DEPLOYED_URL.txt (u otros)."""
    for name in ("DEPLOYED_URL.txt", "URL.txt", "_URL.txt"):
        p = os.path.join(ws, name)
        if os.path.isfile(p):
            try:
                u = open(p, encoding="utf-8").read().strip().splitlines()[0].strip()
                if u.startswith("http"):
                    return u
            except Exception:
                pass
    return None


def _finalize(m: dict, ws: str, ok: bool, spend_usd: float = 0.0,
              iterations: int = 0, result_url=None, note=None) -> None:
    mid = m["id"]
    dest = os.path.join(ROOT, "missions", "done", mid)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.abspath(ws) != os.path.abspath(dest):
        if os.path.exists(dest):
            shutil.rmtree(dest)
        shutil.move(ws, dest)
    # registro (con coste, vueltas, url y motivo si abortó)
    with open(os.path.join(dest, "_RESULT.json"), "w", encoding="utf-8") as f:
        json.dump({"id": mid, "ok": ok, "title": m["title"],
                   "spend_usd": round(spend_usd, 4), "iterations": iterations,
                   "result_url": result_url, "note": note}, f, ensure_ascii=False, indent=2)


def main():
    if len(sys.argv) < 2:
        print("uso: python -m orchestrator.runner <ruta_mission.yaml>")
        sys.exit(1)
    run_mission(sys.argv[1])


if __name__ == "__main__":
    main()
