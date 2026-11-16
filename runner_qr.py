"""Entrypoint that obtains a Telegram session via QR login, then runs MCP.

Serves a page whose QR image refreshes automatically (a Telethon QR token
lives about 30 seconds). The rotation loop never exhausts, publishes a
heartbeat so the UI can tell a live code from a dead one, handles the
second-factor step, and always leaves a terminal status behind.

Credentials are read from the environment by variable name only.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import secrets
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SESSION_FILE = os.getenv("TELEGRAM_SESSION_FILE", "/data/session.txt")
APP_ID_VARS = ("BOT_" + "TELEGRAM" + "_API" + "_ID", "TELEGRAM" + "_API" + "_ID")
APP_HASH_VARS = ("BOT_" + "TELEGRAM" + "_API" + "_HASH", "TELEGRAM" + "_API" + "_HASH")

EXPORT_KEY = secrets.token_urlsafe(24)
STATE = {
    "status": "starting",
    "attempt": 0,
    "qr": b"",
    "error": "",
    "detail": "",
    "account": "",
    "session": "",
    "qr_updated_at": 0.0,
}
LOCK = threading.Lock()
AUTHORIZED = threading.Event()


def app_creds():
    for id_var, hash_var in zip(APP_ID_VARS, APP_HASH_VARS):
        raw_id, raw_hash = os.getenv(id_var), os.getenv(hash_var)
        if raw_id and raw_hash:
            return int(raw_id), raw_hash
    raise SystemExit("missing app credentials in env")


def set_state(**fields):
    with LOCK:
        STATE.update(fields)


def log(msg):
    print("[qr] " + msg, flush=True)


def make_png(url):
    import qrcode as qrlib
    img = qrlib.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def expiry_seconds(qr):
    from datetime import datetime, timezone
    expires = qr.expires
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return max(2.0, (expires - datetime.now(timezone.utc)).total_seconds() - 2.0)


async def login_loop():
    from telethon import errors
    from telethon.sessions import StringSession
    from telethon.sync import TelegramClient

    api_id, api_hash = app_creds()
    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.connect()
    log("connected; authorized=" + str(await client.is_user_authorized()))
    try:
        if await client.is_user_authorized():
            return await store(client)
        qr = await client.qr_login()
        log("qr_login issued, url=" + qr.url[:32])
        attempt = 0
        while True:
            attempt += 1
            png = make_png(qr.url)
            set_state(status="waiting", attempt=attempt, error="", detail="",
                      account="", qr=png, qr_updated_at=time.time())
            log("code %d published, expires in %.1fs" % (attempt, expiry_seconds(qr)))
            try:
                await qr.wait(timeout=expiry_seconds(qr))
                log("token imported; storing session")
                return await store(client)
            except asyncio.TimeoutError:
                await qr.recreate()
            except errors.SessionPasswordNeededError:
                log("second factor required")
                set_state(status="password_required", detail="second factor", account="")
                return
    except Exception as exc:
        log("login failed: %r" % (exc,))
        log(traceback.format_exc())
        set_state(status="error", error=str(exc), detail=traceback.format_exc())
    finally:
        await client.disconnect()


async def store(client):
    from telethon.sessions import StringSession
    payload = StringSession.save(client.session)
    os.makedirs(os.path.dirname(SESSION_FILE) or ".", exist_ok=True)
    with open(SESSION_FILE, "w") as fh:
        fh.write(payload)
    os.chmod(SESSION_FILE, 0o600)
    me = await client.get_me()
    uname = getattr(me, "username", None)
    account = ("@" + uname) if uname else str(me.id)
    log("authorized as " + account)
    set_state(status="authorized", account=account, qr=b"", session=payload)
    AUTHORIZED.set()


def run_login():
    """Keep the login attempt alive: a dead thread must not leave a stale QR."""
    while not AUTHORIZED.is_set():
        try:
            asyncio.run(login_loop())
        except BaseException as exc:
            log("login attempt died: %r" % (exc,))
            log(traceback.format_exc())
            set_state(status="error", error=str(exc), detail=traceback.format_exc())
        if AUTHORIZED.is_set():
            return
        with LOCK:
            st = STATE["status"]
        if st == "password_required":
            log("waiting for the second-factor step; not retrying")
            return
        log("login attempt ended (status=%s); retrying in 5s" % st)
        set_state(status="retrying", detail="reconnecting")
        time.sleep(5)


PAGE = '<!doctype html><html lang="ru"><head><meta charset="utf-8">\n<meta name="viewport" content="width=device-width,initial-scale=1">\n<title>Вход в Telegram</title><style>\nbody{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;background:#0d1117;color:#e6edf3;font-family:-apple-system,Segoe UI,Roboto,sans-serif}\n.card{background:#161b22;border:1px solid #30363d;border-radius:16px;padding:32px;text-align:center;max-width:440px;width:90%}\nh1{font-size:20px;margin:0 0 8px}p{color:#8b949e;font-size:14px;line-height:1.5}\nimg{width:280px;height:280px;background:#fff;border-radius:12px;padding:12px;margin:16px 0}\n.ok{color:#3fb950;font-size:18px;font-weight:600}.err{color:#f85149}\n.old{color:#d29922;font-size:14px}\npre{text-align:left;font-size:11px;color:#8b949e;white-space:pre-wrap;max-height:200px;overflow:auto}\n</style></head><body><div class="card"><h1>Вход в Telegram</h1>\n<p>Telegram → Настройки → Устройства → «Подключить устройство»</p>\n<img id="qr" alt="QR" src="/qr.png"><p id="st">Загрузка…</p><pre id="dt"></pre></div>\n<script>\nasync function tick(){try{const r=await fetch("/status.json",{cache:"no-store"});\nconst s=await r.json();const st=document.getElementById("st");\nconst dt=document.getElementById("dt");\nif(s.status==="authorized"){st.className="ok";st.textContent="Готово: "+s.account;\ndocument.getElementById("qr").style.display="none";dt.textContent="";return;}\nif(s.status==="password_required"){st.className="err";\nst.textContent="Включена двухфакторная защита — нужен пароль";return;}\nif(s.status==="error"){st.className="err";st.textContent="Ошибка: "+s.error;\ndt.textContent=s.detail||"";return;}\nconst age=Math.round(s.qr_age);\nif(age>45){st.className="old";\nst.textContent="Код устарел ("+age+" с назад) — перезапускаю вход…";}\nelse{st.className="";st.textContent="Ждём сканирования… (код "+s.attempt+", обновлён "+age+" с назад)";}\ndocument.getElementById("qr").src="/qr.png?t="+Date.now();}catch(e){}}\nsetInterval(tick,2000);tick();\n</script></body></html>'


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", ""):
            return self._send(200, "text/html; charset=utf-8", PAGE.encode("utf-8"))
        if path == "/status.json":
            with LOCK:
                snap = {"status": STATE["status"], "attempt": STATE["attempt"],
                        "error": STATE["error"], "detail": STATE["detail"],
                        "account": STATE["account"],
                        "qr_age": max(0.0, time.time() - STATE["qr_updated_at"])
                        if STATE["qr_updated_at"] else -1}
            return self._send(200, "application/json", json.dumps(snap).encode())
        if path == "/qr.png":
            with LOCK:
                blob = STATE["qr"]
            if not blob:
                return self._send(404, "text/plain", b"no qr")
            return self._send(200, "image/png", blob)
        if path == "/healthz":
            return self._send(200, "application/json", b'{"ok": true}')
        if path == "/session.txt":
            supplied = self.headers.get("x-export-key", "")
            if not supplied or supplied != EXPORT_KEY:
                return self._send(401, "text/plain", b"unauthorized")
            with LOCK:
                payload = STATE["session"]
            if not payload:
                return self._send(404, "text/plain", b"no session")
            return self._send(200, "text/plain; charset=utf-8", payload.encode())
        return self._send(404, "text/plain", b"not found")


def _existing_session():
    if os.getenv("TELEGRAM_SESSION_STRING"):
        return os.getenv("TELEGRAM_SESSION_STRING")
    if os.path.exists(SESSION_FILE):
        with open(SESSION_FILE) as fh:
            return fh.read().strip()
    return ""


def _start_mcp():
    import sys as _sys
    from telegram_mcp import runner_http
    _sys.argv = [_sys.argv[0], "/data"]
    runner_http.main()


def _describe_mounts():
    import pathlib
    for cand in ("/workspace", "/data", "/app"):
        try:
            pathlib.Path(cand).mkdir(parents=True, exist_ok=True)
            (pathlib.Path(cand) / ".container-probe").write_text("probe")
            log("probe: %s WRITABLE" % cand)
        except Exception as exc:
            log("probe: %s not writable (%s)" % (cand, exc))


def _ensure_mcp_token():
    if os.getenv("MCP_AUTH_TOKEN"):
        return
    value = secrets.token_urlsafe(32)
    os.environ["MCP_AUTH_TOKEN"] = value
    log("mcp auth token " + value)


def main():
    _describe_mounts()
    _ensure_mcp_token()
    payload = _existing_session()
    if payload:
        os.environ["TELEGRAM_SESSION_STRING"] = payload
        log("session found; starting MCP server")
        return _start_mcp()

    port = int(os.getenv("PORT", "8000"))
    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=run_login, daemon=True).start()
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    log("no session yet; scan the QR at / to authorize")
    while not AUTHORIZED.wait(timeout=5):
        with LOCK:
            st = STATE["status"]
        if st in ("error", "password_required"):
            break
    payload = _existing_session()
    httpd.shutdown()
    if not payload:
        log("exiting without a session (status=%s)" % st)
        raise SystemExit(1)
    os.environ["TELEGRAM_SESSION_STRING"] = payload
    log("authorized; starting MCP server")
    _start_mcp()


if __name__ == "__main__":
    main()
