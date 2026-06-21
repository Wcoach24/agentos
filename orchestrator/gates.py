"""
gates.py — Plano de control human-in-the-loop. Canal principal: TELEGRAM (botones
GO/NO, acuse y consumo idempotente por offset). Fallback: email SMTP/IMAP.

Por qué Telegram: el research marcó el polling IMAP como el eslabón más frágil
(orden/unicidad no garantizados, quoting que inyecta [GO] de hilos previos, doble
lectura = doble side-effect). getUpdates con offset da consumo exactly-once y el
inline keyboard da acuse real. Se elige con GATE_CHANNEL en el .env.

El grafo NO consume crédito Agent SDK mientras espera aquí: es solo API de Telegram/IMAP.
"""
from __future__ import annotations
import os, ssl, time, json, imaplib, smtplib, email, urllib.request
from email.message import EmailMessage
from dataclasses import dataclass

# ---- email (fallback) ----
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
IMAP_HOST = os.environ.get("IMAP_HOST", "imap.gmail.com")
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
DISPATCHER_EMAIL = os.environ.get("DISPATCHER_EMAIL", SMTP_USER)

# ---- telegram (principal) ----
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
_STATE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state")
_OFFSET_FILE = os.path.join(_STATE, "tg_offset")


@dataclass
class GateDecision:
    approved: bool
    raw_reply: str


def _channel() -> str:
    ch = os.environ.get("GATE_CHANNEL", "telegram" if TG_TOKEN else "email").lower()
    if ch == "telegram" and TG_TOKEN and TG_CHAT:
        return "telegram"
    return "email"


# ----------------------------------------------------------------- telegram
def _tg_api(method: str, params: dict, timeout: int = 70) -> dict:
    url = f"https://api.telegram.org/bot{TG_TOKEN}/{method}"
    data = json.dumps(params).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _tg_send(text: str, buttons: bool = False, mission_id: str = "") -> dict:
    params: dict = {"chat_id": TG_CHAT, "text": text[:4000], "disable_web_page_preview": True}
    if buttons:
        params["reply_markup"] = {"inline_keyboard": [[
            {"text": "✅ GO", "callback_data": f"GO|{mission_id}"},
            {"text": "❌ NO", "callback_data": f"NO|{mission_id}"},
        ]]}
    return _tg_api("sendMessage", params, timeout=30)


def _load_offset() -> int:
    try:
        return int(open(_OFFSET_FILE, encoding="utf-8").read().strip())
    except Exception:
        return 0


def _save_offset(o: int) -> None:
    try:
        os.makedirs(_STATE, exist_ok=True)
        open(_OFFSET_FILE, "w", encoding="utf-8").write(str(o))
    except Exception:
        pass


# --- comandos /idea (bridge desde cualquier sitio) ---
_PENDING_CMDS = os.path.join(_STATE, "pending_commands.jsonl")


def _is_command(text: str) -> str | None:
    """Si el texto es '/idea ...' o '/mission ...', devuelve la idea (resto). Si no, None."""
    t = (text or "").strip()
    low = t.lower()
    for p in ("/idea", "/mission"):
        if low.startswith(p):
            parts = t.split(None, 1)
            return parts[1].strip() if len(parts) > 1 else ""
    return None


def _queue_command(idea: str) -> None:
    try:
        os.makedirs(_STATE, exist_ok=True)
        with open(_PENDING_CMDS, "a", encoding="utf-8") as f:
            f.write(json.dumps({"idea": idea, "ts": time.time()}, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _pop_pending() -> str | None:
    try:
        if not os.path.isfile(_PENDING_CMDS):
            return None
        lines = [l for l in open(_PENDING_CMDS, encoding="utf-8").read().splitlines() if l.strip()]
        if not lines:
            return None
        first, rest = lines[0], lines[1:]
        open(_PENDING_CMDS, "w", encoding="utf-8").write(("\n".join(rest) + "\n") if rest else "")
        return json.loads(first).get("idea")
    except Exception:
        return None


def _parse_update(upd: dict, mission_id: str) -> GateDecision | None:
    cq = upd.get("callback_query")
    if cq:
        data = cq.get("data", "")
        if "|" in data:
            verdict, mid = data.split("|", 1)
            if mid == mission_id or not mission_id:
                try:
                    _tg_api("answerCallbackQuery",
                            {"callback_query_id": cq["id"], "text": f"{verdict} registrado ✓"}, timeout=15)
                except Exception:
                    pass
                return GateDecision(verdict.upper() == "GO", data)
        return None
    msg = upd.get("message") or {}
    if str(msg.get("chat", {}).get("id")) == str(TG_CHAT):
        raw = msg.get("text") or ""
        cmd = _is_command(raw)
        if cmd is not None:
            _queue_command(cmd)   # /idea durante un gate -> se reencola, NO se pierde
            return None
        t = raw.strip().upper()
        if "[GO]" in t or t == "GO":
            return GateDecision(True, raw)
        if "[NO]" in t or t == "NO":
            return GateDecision(False, raw)
    return None


def _tg_wait_decision(mission_id: str, timeout_hours: float = 72) -> GateDecision:
    deadline = time.time() + timeout_hours * 3600
    offset = _load_offset()
    while time.time() < deadline:
        try:
            resp = _tg_api("getUpdates", {"offset": offset, "timeout": 50}, timeout=70)
        except Exception:
            time.sleep(3); continue
        for upd in resp.get("result", []):
            offset = max(offset, upd["update_id"] + 1)
            _save_offset(offset)  # consumo idempotente: no se relee la misma respuesta
            dec = _parse_update(upd, mission_id)
            if dec is not None:
                return dec
    return GateDecision(False, "TIMEOUT: sin respuesta humana, gate denegado por seguridad.")


# ----------------------------------------------------------------- email (fallback)
def _email_send_gate(mission_id: str, subject: str, body: str, to: str | None = None) -> None:
    to = to or DISPATCHER_EMAIL
    msg = EmailMessage()
    msg["From"] = SMTP_USER
    msg["To"] = to
    msg["Subject"] = f"[GATE] {mission_id} — {subject}"
    msg.set_content(body + "\n\n---\nResponde con [GO] para aprobar o [NO] para rechazar.\n")
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx) as s:
        s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)


def _email_check(mission_id: str) -> GateDecision | None:
    with imaplib.IMAP4_SSL(IMAP_HOST) as im:
        im.login(SMTP_USER, SMTP_PASS)
        im.select("INBOX")
        typ, data = im.search(None, "UNSEEN", "BODY", mission_id)
        if typ != "OK" or not data or not data[0]:
            return None
        for num in data[0].split():
            typ, raw = im.fetch(num, "(RFC822)")
            if typ != "OK":
                continue
            m = email.message_from_bytes(raw[0][1])
            body = _extract_text(m).upper()
            if "[GO]" in body:
                return GateDecision(True, _extract_text(m))
            if "[NO]" in body:
                return GateDecision(False, _extract_text(m))
        return None


def _extract_text(m: "email.message.Message") -> str:
    if m.is_multipart():
        for part in m.walk():
            if part.get_content_type() == "text/plain":
                return part.get_payload(decode=True).decode(errors="ignore")
        return ""
    return m.get_payload(decode=True).decode(errors="ignore")


def _email_wait(mission_id: str, poll_seconds: int = 60, timeout_hours: float = 72) -> GateDecision:
    deadline = time.time() + timeout_hours * 3600
    while time.time() < deadline:
        decision = _email_check(mission_id)
        if decision is not None:
            return decision
        time.sleep(poll_seconds)
    return GateDecision(False, "TIMEOUT: sin respuesta humana, gate denegado por seguridad.")


# ----------------------------------------------------------------- API pública
def send_gate(mission_id: str, subject: str, body: str, to: str | None = None,
              decision: bool = False) -> None:
    """Manda el [GATE] (decision=True -> botones GO/NO) o una notificación (decision=False).
    Telegram si está configurado; si falla, cae a email."""
    if _channel() == "telegram":
        try:
            head = f"🔔 [GATE] {mission_id}\n{subject}" if decision else f"📣 {subject} — {mission_id}"
            _tg_send(f"{head}\n\n{body[:1500]}", buttons=decision, mission_id=mission_id)
            return
        except Exception as e:
            print(f"[gates] Telegram falló ({e}); fallback a email")
    _email_send_gate(mission_id, subject, body, to)


def wait_for_decision(mission_id: str, poll_seconds: int = 60, timeout_hours: float = 72) -> GateDecision:
    """Bloquea (polling barato) hasta GO/NO o timeout. Sin gasto de crédito Agent SDK.
    Escucha DOS canales a la vez: el dashboard (tabla agentos_commands) y Telegram/email.
    Lo que llegue primero decide."""
    deadline = time.time() + timeout_hours * 3600
    tg = _channel() == "telegram"
    offset = _load_offset() if tg else 0
    while time.time() < deadline:
        # 1) ¿GO/NO desde el dashboard?
        try:
            from . import control
            d = control.gate_decision(mission_id)
            if d is not None:
                return GateDecision(bool(d), "Decisión desde el dashboard: " + ("GO" if d else "NO"))
        except Exception:
            pass
        # 2) canal principal
        if tg:
            try:
                resp = _tg_api("getUpdates", {"offset": offset, "timeout": 20}, timeout=30)
                for upd in resp.get("result", []):
                    offset = max(offset, upd["update_id"] + 1)
                    _save_offset(offset)  # consumo idempotente
                    dec = _parse_update(upd, mission_id)
                    if dec is not None:
                        return dec
            except Exception:
                time.sleep(3)
        else:
            try:
                dec = _email_check(mission_id)
                if dec is not None:
                    return dec
            except Exception:
                pass
            time.sleep(poll_seconds)
    return GateDecision(False, "TIMEOUT: sin respuesta humana, gate denegado por seguridad.")


def next_command(timeout: int = 2) -> str | None:
    """Siguiente idea '/idea ...' pendiente (drena cola + un getUpdates corto). Lo usa el
    watcher cuando está OCIOSO, para no chocar con el polling de gates del runner (serie)."""
    cmd = _pop_pending()
    if cmd is not None:
        return cmd
    if _channel() != "telegram":
        return None
    offset = _load_offset()
    try:
        resp = _tg_api("getUpdates", {"offset": offset, "timeout": timeout}, timeout=timeout + 5)
    except Exception:
        return None
    for upd in resp.get("result", []):
        offset = max(offset, upd["update_id"] + 1)
        _save_offset(offset)
        _parse_update(upd, "")   # rutea: /idea -> cola; GO/NO ociosos -> se descartan
    return _pop_pending()


def notify(text: str) -> None:
    """Aviso suelto (sin botones). Telegram si está configurado; si no, email."""
    if _channel() == "telegram":
        try:
            _tg_send(text)
            return
        except Exception:
            pass
    try:
        _email_send_gate("aviso", "AgentOS", text)
    except Exception:
        pass
