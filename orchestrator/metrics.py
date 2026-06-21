"""
metrics.py — vuelca estado + coste de cada misión a Supabase (proyecto dedicado),
para que el dashboard web los lea. PostgREST upsert con la service key.
No-op si SUPABASE_URL / SUPABASE_SERVICE_KEY no están configurados.
"""
from __future__ import annotations
import os, json, urllib.request
from datetime import datetime, timezone


def push_mission(mid: str, title: str, status: str, ok: bool,
                 spend_usd=None, iterations=None, result_url=None, note=None,
                 node=None, last_action=None, dod=None) -> None:
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not (url and key):
        return
    # Upsert PARCIAL: solo incluimos los campos con valor. Así el push de ARRANQUE
    # ('active' sin iterations/spend) NO machaca el progreso ya registrado a 0 — antes
    # cada reanudación reseteaba el contador del dashboard, que es justo lo que confundía.
    row = {
        "id": mid, "title": (title or "")[:200], "status": status, "ok": bool(ok),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if spend_usd is not None:
        row["spend_usd"] = round(float(spend_usd or 0.0), 4)
    if iterations is not None:
        row["iterations"] = int(iterations or 0)
    if result_url is not None:
        row["result_url"] = result_url
    if note is not None:
        row["note"] = str(note)[:500]
    if node is not None:
        row["node"] = str(node)[:60]
    if last_action is not None:
        row["last_action"] = str(last_action)[:600]
    if dod is not None:
        row["dod"] = dod   # lista [{id,passed,evidence}] -> jsonb (para el checklist del drill-down)
    def _post(payload: dict) -> None:
        rq = urllib.request.Request(
            f"{url}/rest/v1/agentos_missions",
            data=json.dumps([payload]).encode("utf-8"), method="POST",
            headers={"apikey": key, "Authorization": f"Bearer {key}",
                     "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates"})
        urllib.request.urlopen(rq, timeout=20).read()
    try:
        _post(row)
    except Exception as e:
        # p.ej. la columna 'note' aún no existe -> reintenta sin ella
        if "note" in row:
            row.pop("note", None)
            try:
                _post(row); return
            except Exception as e2:
                e = e2
        print(f"[metrics] push a Supabase falló: {e}")
