#!/usr/bin/env python3
"""
watcher.py — El puente + lanzador. Vigila missions/inbox/, arranca el runner para
cada misión nueva, y RETOMA las que quedaron en pausa por límite de uso del plan.

EL BRIDGE (cómo cruza una misión desde un chat de Claude.ai al Mac):
  - REPO_SYNC="local" (por defecto): algo deja el yaml en missions/inbox/. En Cowork,
    cuando dices "continúalo solo", Claude escribe el mission.yaml (ya validado) DIRECTO
    en ~/Desktop/os/agent-os/missions/inbox/. Cero git, cero acoplamiento. Es el bridge.
  - REPO_SYNC="git" + REPO_DIR: para chats de Claude.ai SIN acceso al Mac. El watcher
    hace `git pull` del repo cada ciclo y copia los yaml de missions/inbox/ a la inbox
    local. (Usa credenciales de solo-lectura propias; NO el token del pipeline de artefactos.)

ORQUESTACIÓN:
  - Serie con prioridad: una misión a la vez; mayor `priority` en el yaml salta la cola.
  - Retoma pausadas: si una misión se pausó por rate-limit del plan (runner exit 75),
    queda en active/ con _PAUSED.json; el watcher la reintenta tras PAUSE_BACKOFF.
  - Coste: el SDK consume de tu cuota Max compartida -> serie protege tu Claude interactivo.
"""
from __future__ import annotations
import os, sys, time, json, subprocess, shutil, glob, urllib.request, sqlite3
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../agent-os
sys.path.insert(0, ROOT)  # para 'from orchestrator import gates' al correr como script
INBOX = os.path.join(ROOT, "missions", "inbox")
ACTIVE = os.path.join(ROOT, "missions", "active")
PROCESSED = os.path.join(ROOT, "missions", "_processed")
HELD = os.path.join(ROOT, "missions", "_held")          # misiones en hold desde el dashboard
CHECKPOINT_DB = os.path.join(ROOT, "state", "checkpoints.sqlite")
DONE_LEDGER = os.path.join(ROOT, "state", "done_ids.txt")  # idempotencia: id -> ya ejecutada


def _load_done() -> set:
    try:
        return set(x.strip() for x in open(DONE_LEDGER, encoding="utf-8") if x.strip())
    except Exception:
        return set()


def _mark_done(mid: str) -> None:
    """Registra que una misión ya corrió (idempotencia): no se re-ejecuta aunque su yaml
    reaparezca en el inbox (p.ej. tras un re-sync). El retry explícito la des-registra."""
    try:
        os.makedirs(os.path.dirname(DONE_LEDGER), exist_ok=True)
        if mid not in _load_done():
            with open(DONE_LEDGER, "a", encoding="utf-8") as f:
                f.write(mid + "\n")
    except Exception:
        pass


def _unmark_done(mid: str) -> None:
    s = _load_done(); s.discard(mid)
    try:
        with open(DONE_LEDGER, "w", encoding="utf-8") as f:
            f.write("".join(x + "\n" for x in sorted(s)))
    except Exception:
        pass


def _seed_ledger_from_history() -> None:
    """Al arrancar, marca como 'hechas' las misiones que ya están en _processed/ o done/.
    Idempotencia retroactiva: un re-sync que reañada esos yaml NO las re-ejecuta."""
    try:
        for y in glob.glob(os.path.join(PROCESSED, "*.yaml")):
            _mark_done(os.path.basename(y)[:-5])
        donedir = os.path.join(ROOT, "missions", "done")
        if os.path.isdir(donedir):
            for d in os.listdir(donedir):
                if os.path.isdir(os.path.join(donedir, d)):
                    _mark_done(d)
    except Exception:
        pass

POLL = int(os.environ.get("WATCHER_POLL_SECONDS", "120"))
PAUSE_BACKOFF = int(os.environ.get("PAUSE_BACKOFF_SECONDS", "900"))  # 15 min: ventana de rate-limit
REPO_SYNC = os.environ.get("REPO_SYNC", "local")   # github_api | git | local
REPO_DIR = os.environ.get("REPO_DIR", "")
REPO_URL = os.environ.get("REPO_URL", "")          # solo modo git: auto-clona en REPO_DIR
GITHUB_REPO = os.environ.get("GITHUB_REPO", "")    # modo github_api: "owner/repo"
MISSIONS_PATH = os.environ.get("MISSIONS_PATH", "missions/inbox")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")  # PAT con contents:read sobre el repo


def _seen(name: str) -> bool:
    """¿Ya conocemos esta misión? (en inbox/active/_processed o ya terminada en done)."""
    for d in (INBOX, ACTIVE, PROCESSED):
        if os.path.exists(os.path.join(d, name)):
            return True
    did = name[:-5] if name.endswith(".yaml") else name
    return os.path.isdir(os.path.join(ROOT, "missions", "done", did))


def _accept_mission(name: str, content: str, via: str) -> None:
    """Valida (misma barrera que el runner) y, si pasa, escribe la misión en la inbox local."""
    try:
        from orchestrator.runner import validate_mission
        validate_mission(yaml.safe_load(content))
    except Exception as e:
        print(f"[watcher] misión RECHAZADA ({name}): {e}")
        try:
            from orchestrator import gates
            gates.notify(f"❌ Misión de claude.ai rechazada ({name}): {str(e)[:160]}")
        except Exception:
            pass
        return
    with open(os.path.join(INBOX, name), "w", encoding="utf-8") as f:
        f.write(content)
    print(f"[watcher] misión recibida ({via}): {name}")
    try:
        from orchestrator import gates
        gates.notify(f"📥 Misión recibida de claude.ai: {name[:-5]}")
    except Exception:
        pass


def _sync_github_api() -> None:
    """BRIDGE por API de GitHub (SIN clon, SIN auth de git): lista <repo>/<MISSIONS_PATH>
    y descarga los .yaml nuevos usando GITHUB_TOKEN (PAT contents:read). El más simple."""
    if not GITHUB_REPO:
        return
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "agentos-watcher"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{MISSIONS_PATH}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
            items = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"[watcher] github API list falló: {e}")
        return
    for it in (items if isinstance(items, list) else []):
        name = it.get("name", "")
        if it.get("type") != "file" or not name.endswith(".yaml") or _seen(name):
            continue
        try:
            with urllib.request.urlopen(urllib.request.Request(it["download_url"], headers=headers), timeout=30) as r:
                content = r.read().decode("utf-8")
        except Exception as e:
            print(f"[watcher] descarga {name} falló: {e}")
            continue
        _accept_mission(name, content, "API")


def _sync_repo() -> None:
    """Dispatcher del bridge según REPO_SYNC: github_api (recomendado) | git | local."""
    if REPO_SYNC == "github_api":
        _sync_github_api()
    elif REPO_SYNC == "git":
        _sync_git()
    # local: nada (Cowork u otro proceso deja el yaml en la inbox)


def _sync_git() -> None:
    """BRIDGE GIT (alternativa pesada): clona/pull el repo + valida + copia a inbox."""
    if not REPO_DIR:
        return
    # git NUNCA debe pedir credenciales interactivas en el daemon (colgaría). Falla rápido.
    genv = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "echo"}
    # auto-clonado: si REPO_DIR no es un repo y tenemos REPO_URL, clónalo (sin pasos manuales)
    if not os.path.isdir(os.path.join(REPO_DIR, ".git")):
        if not REPO_URL:
            return
        try:
            print(f"[watcher] clonando {REPO_URL} -> {REPO_DIR}")
            os.makedirs(os.path.dirname(REPO_DIR) or ".", exist_ok=True)
            r = subprocess.run(["git", "clone", "--depth", "1", REPO_URL, REPO_DIR],
                               check=False, timeout=300, capture_output=True, text=True, env=genv)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.strip()[:200])
        except Exception as e:
            print(f"[watcher] clone falló: {e}")
            try:
                from orchestrator import gates
                gates.notify(f"⚠️ No pude clonar el repo puente ({REPO_URL}). "
                             f"Revisa la auth de git en el Mac (gh auth login / SSH). El bridge "
                             f"local y Telegram siguen funcionando.")
            except Exception:
                pass
            return
    try:
        subprocess.run(["git", "-C", REPO_DIR, "pull", "--quiet"],
                       check=False, timeout=120, env=genv)
    except Exception as e:
        print(f"[watcher] git pull falló: {e}"); return
    src = os.path.join(REPO_DIR, "missions", "inbox")
    if not os.path.isdir(src):
        return
    for y in sorted(glob.glob(os.path.join(src, "*.yaml"))):
        name = os.path.basename(y)
        if _seen(name):
            continue
        try:
            from orchestrator.runner import validate_mission
            m = yaml.safe_load(open(y, "r", encoding="utf-8"))
            validate_mission(m)               # rechaza DoD sin check de máquina (no falseable)
        except Exception as e:
            print(f"[watcher] misión de repo RECHAZADA ({name}): {e}")
            try:
                from orchestrator import gates
                gates.notify(f"❌ Misión de claude.ai rechazada ({name}): {str(e)[:160]}")
            except Exception:
                pass
            continue
        shutil.copy2(y, INBOX)
        print(f"[watcher] misión recibida de repo: {name}")
        try:
            from orchestrator import gates
            gates.notify(f"📥 Misión recibida de claude.ai: {name[:-5]}")
        except Exception:
            pass


def _recover_orphans() -> None:
    """AUTO-RECUPERACIÓN al arrancar: una misión en active/ SIN _PAUSED.json y SIN
    _WAITING_GATE quedó huérfana (el daemon murió o fue matado mientras la corría).
    Antes esto dejaba un fantasma 'active' para siempre que bloqueaba la percepción del
    sistema. Ahora la abandonamos limpio y avisamos, para que el daemon siga sano."""
    for yaml_path in glob.glob(os.path.join(ACTIVE, "*.yaml")):
        mid = os.path.basename(yaml_path)[:-5]
        ws = os.path.join(ACTIVE, mid)
        if os.path.isfile(os.path.join(ws, "_PAUSED.json")):
            continue   # pausada legítima por rate-limit -> la retoma _next_paused()
        if os.path.isfile(os.path.join(ws, "_WAITING_GATE")):
            continue   # esperaba GO/NO; la dejamos para que se reanude
        try:
            shutil.move(yaml_path, os.path.join(PROCESSED, os.path.basename(yaml_path)))
        except Exception:
            pass
        print(f"[watcher] huérfana recuperada (abandonada tras reinicio): {mid}")
        try:
            from orchestrator import gates, metrics
            gates.notify(f"♻️ Al arrancar encontré la misión '{mid}' a medias (el daemon se "
                         f"reinició mientras corría). La he abandonado para no bloquear; "
                         f"si la quieres, vuelve a lanzarla.")
            metrics.push_mission(mid, mid, "aborted", False, note="huérfana tras reinicio del daemon")
        except Exception:
            pass


def _priority(yaml_path: str) -> int:
    try:
        with open(yaml_path, "r", encoding="utf-8") as f:
            return int((yaml.safe_load(f) or {}).get("priority", 0))
    except Exception:
        return 0


def _next_paused() -> str | None:
    """Una misión pausada (active/<id>/_PAUSED.json) cuyo backoff ya pasó -> retomar."""
    now = time.time()
    best = None
    for marker in glob.glob(os.path.join(ACTIVE, "*", "_PAUSED.json")):
        mid = os.path.basename(os.path.dirname(marker))
        yaml_path = os.path.join(ACTIVE, mid + ".yaml")
        if not os.path.isfile(yaml_path):
            continue
        try:
            ts = json.load(open(marker, encoding="utf-8")).get("ts", 0)
        except Exception:
            ts = 0
        if now - ts >= PAUSE_BACKOFF:
            # prioriza la pausada de mayor priority
            if best is None or _priority(yaml_path) > _priority(best):
                best = yaml_path
    return best


def _claim_next_inbox() -> str | None:
    os.makedirs(INBOX, exist_ok=True); os.makedirs(ACTIVE, exist_ok=True); os.makedirs(PROCESSED, exist_ok=True)
    candidates = glob.glob(os.path.join(INBOX, "*.yaml"))
    if not candidates:
        return None
    # mayor prioridad primero; a igualdad, por nombre (fecha en el slug -> FIFO)
    candidates.sort(key=lambda p: (-_priority(p), os.path.basename(p)))
    done = _load_done()
    for src in candidates:
        mid = os.path.basename(src)[:-5]
        if mid in done:
            # IDEMPOTENCIA: ya ejecutada -> sácala del inbox SIN re-correr (evita el
            # 're-arranque' de misiones viejas que reaparecen tras un re-sync).
            try:
                shutil.move(src, os.path.join(PROCESSED, os.path.basename(src)))
            except Exception:
                try: os.remove(src)
                except Exception: pass
            continue
        dst = os.path.join(ACTIVE, os.path.basename(src))
        shutil.move(src, dst)  # claim atómico: salir de inbox
        return dst
    return None


def _clear_checkpoint(mid: str) -> None:
    """Borra el checkpoint del thread para que un retry empiece de CERO (no reanude)."""
    try:
        con = sqlite3.connect(CHECKPOINT_DB)
        for t in ("writes", "checkpoint_blobs", "checkpoints"):
            try: con.execute(f"DELETE FROM {t} WHERE thread_id=?", (mid,))
            except Exception: pass
        con.commit(); con.close()
    except Exception:
        pass


def _find_yaml(mid: str):
    for base in (INBOX, ACTIVE, PROCESSED, HELD):
        p = os.path.join(base, mid + ".yaml")
        if os.path.isfile(p):
            return p
    return None


def _apply_command(cmd: dict) -> str:
    """Ejecuta un comando del dashboard que NO sea 'abortar la misión en curso'
    (eso lo hace _run en caliente). Devuelve texto de resultado."""
    from orchestrator import metrics, control
    act = cmd.get("action", ""); mid = (cmd.get("mission_id") or "").strip()
    args = cmd.get("args") or {}
    if act in ("freeze", "unfreeze"):
        control.set_frozen(act == "freeze")
        return f"daemon {'congelado' if act=='freeze' else 'descongelado'}"
    if act == "priority" and mid:
        p = os.path.join(INBOX, mid + ".yaml")
        if os.path.isfile(p):
            m = yaml.safe_load(open(p, encoding="utf-8")) or {}
            m["priority"] = int(args.get("priority", 100))
            yaml.safe_dump(m, open(p, "w", encoding="utf-8"), allow_unicode=True, sort_keys=False)
            return f"prioridad {mid} -> {m['priority']}"
        return f"{mid} no está en cola"
    if act in ("hold", "unhold") and mid:
        src = os.path.join(INBOX if act == "hold" else HELD, mid + ".yaml")
        dstdir = HELD if act == "hold" else INBOX
        if os.path.isfile(src):
            os.makedirs(dstdir, exist_ok=True)
            shutil.move(src, os.path.join(dstdir, mid + ".yaml"))
            return f"{mid} {'en hold' if act=='hold' else 'reanudada'}"
        return f"{mid} no disponible para {act}"
    if act == "retry" and mid:
        y = _find_yaml(mid)
        if not y:
            return f"sin yaml para reintentar {mid}"
        _unmark_done(mid)   # permite que vuelva a correr (anula la idempotencia para este id)
        _clear_checkpoint(mid)
        for d in (os.path.join(ACTIVE, mid), os.path.join(ROOT, "missions", "done", mid)):
            if os.path.isdir(d):
                try: shutil.rmtree(d)
                except Exception: pass
        os.makedirs(INBOX, exist_ok=True)
        shutil.move(y, os.path.join(INBOX, mid + ".yaml"))
        return f"{mid} reencolada de cero"
    if act == "abort" and mid:
        y = _find_yaml(mid)
        if y:
            os.makedirs(PROCESSED, exist_ok=True)
            shutil.move(y, os.path.join(PROCESSED, mid + ".yaml"))
        d = os.path.join(ACTIVE, mid)
        if os.path.isdir(d):
            try: shutil.rmtree(d)
            except Exception: pass
        metrics.push_mission(mid, mid, "aborted", False, note="abortada desde el dashboard")
        return f"{mid} abortada"
    return f"acción ignorada: {act}"


def _consume_commands(active_mid: str = None) -> bool:
    """Aplica comandos pendientes del dashboard. Devuelve True si hay que MATAR la misión
    en curso (abort de active_mid). approve/reject_gate se dejan para el runner (gates.py)."""
    try:
        from orchestrator import control
        cmds = control.fetch_pending_commands()
    except Exception:
        return False
    kill = False
    for cmd in cmds:
        act = cmd.get("action"); mid = (cmd.get("mission_id") or "").strip()
        if act in ("approve_gate", "reject_gate"):
            continue  # los consume el runner mientras espera el gate
        if act == "abort" and mid and mid == active_mid:
            kill = True
        else:
            try:
                print(f"[watcher] cmd {act} {mid}: {_apply_command(cmd)}")
            except Exception as e:
                print(f"[watcher] cmd {act} error: {e}")
        try:
            control.finish_command(cmd["id"])
        except Exception:
            pass
    return kill


def _run(mission_path: str) -> int:
    print(f"[watcher] lanzando misión: {mission_path}")
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)  # forzar plan, no API key
    try:
        from orchestrator import gates
        gates.notify(f"▶️ Ejecutando misión: {os.path.basename(mission_path)[:-5]}")
    except Exception:
        pass
    # BACKSTOP DE AUTONOMÍA: ninguna misión puede bloquear el daemon para siempre.
    # Lanzamos el runner como subproceso y lo vigilamos: si lleva demasiado tiempo SIN
    # avanzar y NO está esperando un gate (marca _WAITING_GATE), lo matamos y seguimos.
    timeout_s = int(os.environ.get("WATCHER_RUNNER_TIMEOUT_SECONDS", "7200"))  # 2h
    mid = os.path.basename(mission_path)[:-5]
    gate_marker = os.path.join(ACTIVE, mid, "_WAITING_GATE")
    proc = subprocess.Popen(
        [sys.executable, "-m", "orchestrator.runner", mission_path],
        cwd=ROOT, env=env)
    start = time.time()
    killed = False
    kill_reason = None
    while True:
        try:
            rc = proc.wait(timeout=15)
            break
        except subprocess.TimeoutExpired:
            at_gate = os.path.exists(gate_marker)
            if at_gate:
                start = time.time()   # esperando humano (gate): no es cuelgue, reinicia el reloj
            elif time.time() - start > timeout_s:
                kill_reason = "timeout"
            # comandos del dashboard (abortar ESTA misión en caliente)
            try:
                if _consume_commands(mid):
                    kill_reason = "abort"
            except Exception:
                pass
            # latido para el panel de sistema (Supabase) + fichero local
            _touch_heartbeat()
            try:
                from orchestrator import control
                control.push_heartbeat("waiting_gate" if at_gate else "running",
                                       active_mission=mid, frozen=control.is_frozen())
            except Exception:
                pass
            if kill_reason:
                proc.kill()
                try:
                    proc.wait(timeout=10)
                except Exception:
                    pass
                killed = True
                rc = 124
                break
    if killed:
        from orchestrator import gates, metrics
        if kill_reason == "abort":
            print(f"[watcher] misión {mid} abortada desde el dashboard")
            note = "abortada desde el dashboard"
            msg = f"🛑 Misión {mid} abortada desde el dashboard. Sigo con la siguiente."
        else:
            print(f"[watcher] misión {mid} colgada >{timeout_s//60}min; matada")
            note = f"timeout watcher >{timeout_s//60}min"
            msg = (f"⏱️ Misión {mid} colgada >{timeout_s//60} min sin avanzar. "
                   f"La maté y sigo con la siguiente.")
        try: gates.notify(msg)
        except Exception: pass
        try: metrics.push_mission(mid, mid, "aborted", False, note=note)
        except Exception: pass
    if rc == 75:
        # PAUSADA por rate-limit: dejar el yaml en active/, retomar tras backoff.
        print(f"[watcher] misión pausada (límite de uso); reintento en ~{PAUSE_BACKOFF}s")
        return rc
    # terminada (done/aborted): el runner ya movió el workspace a done/
    try:
        shutil.move(mission_path, os.path.join(PROCESSED, os.path.basename(mission_path)))
    except Exception:
        pass
    _mark_done(mid)   # idempotencia: corre UNA vez; no re-arranca si el yaml reaparece
    return rc


def _process_commands() -> bool:
    """Atiende un '/idea ...' de Telegram cuando el watcher está OCIOSO: lo destila en
    misión (dispatcher) y lo encola. Solo cuando no hay misión corriendo, para no chocar
    con el polling de gates del runner."""
    try:
        from orchestrator import gates
        idea = gates.next_command(timeout=2)
    except Exception as e:
        print(f"[watcher] telegram poll error: {e}")
        return False
    if not idea:
        return False
    print(f"[watcher] /idea recibida: {idea[:80]}")
    try:
        gates.notify(f"💡 Idea recibida, destilando en misión…\n«{idea[:200]}»")
        from dispatcher import handle_idea
        msg = handle_idea(idea)
    except Exception as e:
        msg = f"❌ No pude convertir la idea en misión válida: {e}"
    try:
        gates.notify(msg)
    except Exception:
        pass
    return True


def _touch_heartbeat() -> None:
    """Escribe state/watcher_heartbeat.txt cada ciclo: liveness LOCAL comprobable
    (lo usa el check 'heartbeat-fresh' y sirve aunque Supabase no esté disponible)."""
    try:
        os.makedirs(os.path.join(ROOT, "state"), exist_ok=True)
        with open(os.path.join(ROOT, "state", "watcher_heartbeat.txt"), "w") as f:
            f.write(f"{int(time.time())} {time.strftime('%Y-%m-%dT%H:%M:%S')} pid={os.getpid()}\n")
    except Exception:
        pass


def main() -> None:
    print(f"[watcher] vigilando {INBOX} cada {POLL}s (sync={REPO_SYNC}, backoff_pausa={PAUSE_BACKOFF}s)")
    _recover_orphans()   # auto-recuperación: limpia misiones a medias de un reinicio previo
    _seed_ledger_from_history()   # idempotencia: no re-ejecutar lo que ya está hecho
    setup_msg = ""
    try:
        from orchestrator import control
        setup_msg = control.ensure_schema()   # crea/parcha tablas de control (commands+heartbeat)
        print(f"[watcher] supabase: {setup_msg}")
        control.push_heartbeat("starting", frozen=control.is_frozen())
    except Exception as e:
        print(f"[watcher] supabase setup error: {e}")
    try:
        from orchestrator import gates
        gates.notify(f"👀 AgentOS arrancado. Inbox + Telegram + comandos del dashboard (sync={REPO_SYNC}). {setup_msg}")
    except Exception:
        pass
    while True:
        try:
            _touch_heartbeat()           # liveness local cada ciclo
            _sync_repo()
            _consume_commands()          # comandos del dashboard (idle): abort/retry/priority/hold/freeze
            frozen = False
            try:
                from orchestrator import control
                frozen = control.is_frozen()
            except Exception:
                pass
            # freeze global: el daemon no coge nada nuevo NI retoma, solo late
            nxt = None if frozen else (_next_paused() or _claim_next_inbox())
            if nxt:
                _run(nxt)
            elif _process_commands():    # ocioso: atiende /idea de Telegram (bridge)
                continue
            else:
                try:
                    from orchestrator import control
                    control.push_heartbeat("frozen" if frozen else "idle", frozen=frozen)
                except Exception:
                    pass
                time.sleep(POLL)
        except KeyboardInterrupt:
            print("[watcher] parado"); break
        except Exception as e:
            print(f"[watcher] error de ciclo: {e}")
            time.sleep(POLL)


if __name__ == "__main__":
    main()
