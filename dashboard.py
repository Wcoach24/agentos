#!/usr/bin/env python3
"""
dashboard.py — v0. Vista de solo lectura del estado del sistema.

Lee las carpetas missions/ y el checkpoint SQLite. Cero infra, cero dependencias
extra. Para una vista rápida desde la terminal del Mac Mini:

    python dashboard.py            # snapshot único
    python dashboard.py --watch    # refresco cada 5s

El v1 (web con botones GO/NO desde el móvil) se construye encima de esto cuando
el core esté probado. NO lo construyas antes: el core primero.
"""
from __future__ import annotations
import os, sys, json, time, glob, sqlite3

ROOT = os.path.dirname(os.path.abspath(__file__))
MISSIONS = os.path.join(ROOT, "missions")
CHECKPOINT_DB = os.path.join(ROOT, "state", "checkpoints.sqlite")


def _list(folder: str) -> list[str]:
    p = os.path.join(MISSIONS, folder)
    if not os.path.isdir(p):
        return []
    out = []
    for y in sorted(glob.glob(os.path.join(p, "*.yaml"))):
        out.append(os.path.basename(y).replace(".yaml", ""))
    # 'active' y 'done' son carpetas-workspace, no yaml sueltos
    for d in sorted(glob.glob(os.path.join(p, "*/"))):
        out.append(os.path.basename(d.rstrip("/")))
    return sorted(set(out))


def _checkpoint_threads() -> list[str]:
    if not os.path.isfile(CHECKPOINT_DB):
        return []
    try:
        con = sqlite3.connect(CHECKPOINT_DB)
        cur = con.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cur.fetchall()]
        threads = set()
        for t in tables:
            try:
                cur.execute(f"SELECT DISTINCT thread_id FROM {t} LIMIT 100")
                threads.update(r[0] for r in cur.fetchall() if r[0])
            except Exception:
                pass
        con.close()
        return sorted(threads)
    except Exception:
        return []


def _done_result(mid: str) -> str:
    p = os.path.join(MISSIONS, "done", mid, "_RESULT.json")
    if os.path.isfile(p):
        try:
            r = json.load(open(p))
            return "✓ OK" if r.get("ok") else "✗ detenida"
        except Exception:
            return "?"
    return ""


def render() -> str:
    inbox = _list("inbox")
    active = _list("active")
    done = _list("done")
    threads = _checkpoint_threads()
    lines = []
    lines.append("=" * 54)
    lines.append("  AgentOS — estado")
    lines.append("=" * 54)
    lines.append(f"\n  INBOX ({len(inbox)}) — esperando arranque")
    for m in inbox:
        lines.append(f"    · {m}")
    lines.append(f"\n  ACTIVAS ({len(active)}) — en curso o esperando gate")
    for m in active:
        flag = " [checkpoint]" if m in threads else ""
        lines.append(f"    ▶ {m}{flag}")
    lines.append(f"\n  TERMINADAS ({len(done)})")
    for m in done:
        lines.append(f"    {_done_result(m):>10}  {m}")
    if not (inbox or active or done):
        lines.append("\n  (sin misiones todavía)")
    lines.append("\n" + "-" * 54)
    lines.append("  Gates pendientes: revisa tu email [GATE].")
    lines.append("  Logs del watcher: state/watcher.*.log")
    return "\n".join(lines)


def main():
    watch = "--watch" in sys.argv
    if not watch:
        print(render()); return
    try:
        while True:
            os.system("clear")
            print(render())
            print("\n  (--watch: refresco 5s, Ctrl-C para salir)")
            time.sleep(5)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
