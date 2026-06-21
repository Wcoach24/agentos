"""
control.py — Canal de control del dashboard <-> daemon (vía Supabase).

El dashboard es una página pública: NO puede escribir directo en las tablas (RLS lo
bloquea). Para mandar acciones (abortar, reintentar, GO/NO de un gate, freeze…) inserta
filas en `agentos_commands` con una contraseña; una policy RLS `with check (pass = ...)`
deja pasar SOLO si la contraseña coincide, y NO permite leerlas (las lee el daemon con la
service key). Así una página pública puede mandar comandos sin exponer nada peligroso.

Este módulo:
  - ensure_schema(): crea/parchea las tablas (Management API; corre en el Mac, no en el
    sandbox que Cloudflare bloquea). Idempotente -> seguro en cada arranque del daemon.
  - fetch_pending_commands()/finish_command(): la cola que consume el watcher.
  - push_heartbeat(): latido para el panel de sistema.
  - gate_decision(): GO/NO de un gate llegado desde el dashboard (lo usa gates.py).
  - freeze flag local: lo aplica el watcher para dejar de coger misiones nuevas.

No-op silencioso si faltan credenciales de Supabase (el sistema sigue sin dashboard).
"""
from __future__ import annotations
import os, json, urllib.request, urllib.error, urllib.parse
from datetime import datetime, timezone

PASS = os.environ.get("DASH_PASS", "Queso2025")  # contraseña del dashboard (RLS la valida)
_FREEZE_FLAG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "state", "_FROZEN")


def _supa():
    return os.environ.get("SUPABASE_URL", ""), os.environ.get("SUPABASE_SERVICE_KEY", "")


def _req(method, path, body=None, key=None, headers=None):
    url, svc = _supa()
    key = key or svc
    h = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if headers:
        h.update(headers)
    data = json.dumps(body).encode() if body is not None else None
    rq = urllib.request.Request(url + path, data=data, method=method, headers=h)
    r = urllib.request.urlopen(rq, timeout=20)
    raw = r.read().decode()
    return json.loads(raw) if raw else None


# ---------------------------------------------------------------- esquema (Mac-side)
SCHEMA_SQL = """
alter table public.agentos_missions add column if not exists node text;
alter table public.agentos_missions add column if not exists last_action text;
alter table public.agentos_missions add column if not exists dod jsonb;
alter table public.agentos_missions add column if not exists note text;

create table if not exists public.agentos_commands (
  id bigint generated always as identity primary key,
  created_at timestamptz not null default now(),
  action text not null,
  mission_id text,
  args jsonb,
  pass text not null,
  status text not null default 'pending',
  result text
);
alter table public.agentos_commands enable row level security;
drop policy if exists agentos_commands_insert on public.agentos_commands;
create policy agentos_commands_insert on public.agentos_commands
  for insert to anon with check (pass = '%PASS%');
grant insert on public.agentos_commands to anon;

create table if not exists public.agentos_heartbeat (
  id int primary key default 1,
  updated_at timestamptz not null default now(),
  status text, active_mission text, frozen boolean default false, note text,
  constraint agentos_heartbeat_single check (id = 1)
);
alter table public.agentos_heartbeat enable row level security;
drop policy if exists agentos_heartbeat_select on public.agentos_heartbeat;
create policy agentos_heartbeat_select on public.agentos_heartbeat
  for select to anon using (true);
grant select on public.agentos_heartbeat to anon;
insert into public.agentos_heartbeat (id, status) values (1, 'init')
  on conflict (id) do nothing;
""".replace("%PASS%", PASS.replace("'", "''"))


def ensure_schema() -> str:
    """Crea/parcha el esquema vía Management API (corre en el Mac). Idempotente.
    USA curl, NO urllib: Cloudflare bloquea (403 error 1010) la huella TLS de Python en
    api.supabase.com, pero deja pasar curl. Tras el DDL, recarga la caché de PostgREST
    para que el canal anon (insert de comandos) funcione de inmediato."""
    ref = os.environ.get("SUPABASE_PROJECT_REF", "")
    tok = os.environ.get("SUPABASE_ACCESS_TOKEN", "")
    if not (ref and tok):
        return "sin credenciales Management API; salto setup"
    import subprocess, tempfile
    endpoint = f"https://api.supabase.com/v1/projects/{ref}/database/query"
    auth = f"Authorization: Bearer {tok}"
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"query": SCHEMA_SQL}, f); path = f.name
        r = subprocess.run(
            ["curl", "-sS", "-X", "POST", endpoint, "-H", auth,
             "-H", "Content-Type: application/json", "--data-binary", "@" + path],
            capture_output=True, text=True, timeout=45)
        try: os.remove(path)
        except Exception: pass
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        if r.returncode != 0:
            return f"setup esquema: curl rc={r.returncode} {out[:120]}"
        if "1010" in out or '"error"' in out.lower():
            return f"setup esquema avisó: {out[:140]}"
        # recargar caché de PostgREST (si no, el insert anon da 404 hasta el refresco)
        subprocess.run(
            ["curl", "-sS", "-o", "/dev/null", "-X", "POST", endpoint, "-H", auth,
             "-H", "Content-Type: application/json",
             "--data-binary", json.dumps({"query": "notify pgrst, 'reload schema';"})],
            capture_output=True, text=True, timeout=20)
        return "esquema de control listo (commands + heartbeat + columnas) vía curl"
    except FileNotFoundError:
        return "setup esquema: 'curl' no disponible en el Mac"
    except Exception as e:
        return f"setup esquema error: {type(e).__name__}: {e}"


# ---------------------------------------------------------------- cola de comandos
def fetch_pending_commands() -> list:
    url, svc = _supa()
    if not (url and svc):
        return []
    try:
        rows = _req("GET", "/rest/v1/agentos_commands?status=eq.pending&order=created_at.asc")
        return rows or []
    except Exception:
        return []


def finish_command(cmd_id, result: str = "ok") -> None:
    """Marca el comando consumido. Lo borramos para no acumular cola."""
    url, svc = _supa()
    if not (url and svc):
        return
    try:
        _req("DELETE", f"/rest/v1/agentos_commands?id=eq.{cmd_id}")
    except Exception:
        pass


# ---------------------------------------------------------------- latido
def push_heartbeat(status: str, active_mission=None, frozen=False, note=None) -> None:
    url, svc = _supa()
    if not (url and svc):
        return
    row = {"id": 1, "status": status, "active_mission": active_mission,
           "frozen": bool(frozen), "note": note,
           "updated_at": datetime.now(timezone.utc).isoformat()}
    try:
        _req("POST", "/rest/v1/agentos_heartbeat", body=[row],
             headers={"Prefer": "resolution=merge-duplicates"})
    except Exception:
        pass


# ---------------------------------------------------------------- gate GO/NO desde dashboard
def gate_decision(mission_id: str):
    """¿Hay un comando approve_gate/reject_gate para esta misión? Devuelve True/False/None
    y consume el comando. Permite aprobar gates desde el dashboard, no solo por Telegram."""
    url, svc = _supa()
    if not (url and svc):
        return None
    try:
        rows = _req("GET",
            f"/rest/v1/agentos_commands?status=eq.pending&mission_id=eq.{urllib.parse.quote(mission_id)}"
            f"&action=in.(approve_gate,reject_gate)&order=created_at.asc")
    except Exception:
        return None
    if not rows:
        return None
    cmd = rows[0]
    finish_command(cmd["id"])
    return cmd["action"] == "approve_gate"


# ---------------------------------------------------------------- freeze global (flag local)
def is_frozen() -> bool:
    return os.path.isfile(_FREEZE_FLAG)


def set_frozen(on: bool) -> None:
    try:
        os.makedirs(os.path.dirname(_FREEZE_FLAG), exist_ok=True)
        if on:
            open(_FREEZE_FLAG, "w").write("1")
        elif os.path.isfile(_FREEZE_FLAG):
            os.remove(_FREEZE_FLAG)
    except Exception:
        pass
