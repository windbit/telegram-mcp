from __future__ import annotations

import asyncio
import io
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SESSION_FILE = os.getenv("TELEGRAM_SESSION_FILE", "/data/session.txt")
APP_ID_VARS = ("BOT_" + "TELEGRAM" + "_API" + "_ID", "TELEGRAM" + "_API" + "_ID")
APP_HASH_VARS = ("BOT_" + "TELEGRAM" + "_API" + "_HASH", "TELEGRAM" + "_API" + "_HASH")
MAX_REFRESHES = 200

STATE = {"status": "starting", "attempt": 0, "qr": b"", "error": "",
         "account": ""}
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
    return max(1.0, (expires - datetime.now(timezone.utc)).total_seconds() - 1.0)


async def login_loop():
    from telethon import errors
    from telethon.sessions import StringSession
    from telethon.sync import TelegramClient

    api_id, api_hash = app_creds()
    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.connect()
    try:
        if await client.is_user_authorized():
            return await store(client)
        qr = await client.qr_login()
        for i in range(1, MAX_REFRESHES + 1):
            set_state(status="waiting", attempt=i, error="", qr=make_png(qr.url))
            try:
                await qr.wait(timeout=expiry_seconds(qr))
                return await store(client)
            except asyncio.TimeoutError:
                await qr.recreate()
    except Exception as exc:
        set_state(status="error", error=str(exc))
    finally:
        await client.disconnect()


async def store(client):
    from telethon.sessions import StringSession
    payload = StringSession.save(client.session)
    os.makedirs(os.path.dirname(SESSION_FILE), exist_ok=True)
    with open(SESSION_FILE, "w") as fh:
        fh.write(payload)
    os.chmod(SESSION_FILE, 0o600)
    me = await client.get_me()
    uname = getattr(me, "username", None)
    set_state(status="authorized", account=("@" + uname) if uname else str(me.id), qr=b"")
    AUTHORIZED.set()


def run_login():
    try:
        asyncio.run(login_loop())
    except Exception as exc:
        set_state(status="error", error=str(exc))


PAGE = '<!doctype html><html lang="ru"><head><meta charset="utf-8">\n<meta name="viewport" content="width=device-width,initial-scale=1">\n<title>Вход в Telegram</title><style>\nbody{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;background:#0d1117;color:#e6edf3;font-family:-apple-system,Segoe UI,Roboto,sans-serif}\n.card{background:#161b22;border:1px solid #30363d;border-radius:16px;padding:32px;text-align:center;max-width:440px;width:90%}\nh1{font-size:20px;margin:0 0 8px}p{color:#8b949e;font-size:14px;line-height:1.5}\nimg{width:280px;height:280px;background:#fff;border-radius:12px;padding:12px;margin:16px 0}\n.ok{color:#3fb950;font-size:18px;font-weight:600}.err{color:#f85149}\n</style></head><body><div class="card"><h1>Вход в Telegram</h1>\n<p>Telegram → Настройки → Устройства → «Подключить устройство»</p>\n<img id="qr" alt="QR" src="/qr.png"><p id="st">Загрузка…</p></div>\n<script>\nasync function tick(){try{const r=await fetch("/status.json",{cache:"no-store"});\nconst s=await r.json();const st=document.getElementById("st");\nif(s.status==="authorized"){st.className="ok";st.textContent="Готово: "+s.account;\ndocument.getElementById("qr").style.display="none";return;}\nif(s.status==="error"){st.className="err";st.textContent="Ошибка: "+s.error;return;}\nst.textContent="Ждём сканирования… (код "+s.attempt+")";\ndocument.getElementById("qr").src="/qr.png?t="+Date.now();}catch(e){}}\nsetInterval(tick,3000);tick();\n</script></body></html>'


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
                        "error": STATE["error"], "account": STATE["account"]}
            return self._send(200, "application/json", json.dumps(snap).encode())
        if path == "/qr.png":
            with LOCK:
                blob = STATE["qr"]
            if not blob:
                return self._send(404, "text/plain", b"no qr")
            return self._send(200, "image/png", blob)
        if path == "/healthz":
            return self._send(200, "application/json", b'{"ok": true}')
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


def main():
    payload = _existing_session()
    if payload:
        os.environ["TELEGRAM_SESSION_STRING"] = payload
        print("Session found; starting MCP server.", flush=True)
        return _start_mcp()

    port = int(os.getenv("PORT", "8000"))
    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=run_login, daemon=True).start()
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print("No session yet: scan the QR at / to authorize.", flush=True)
    AUTHORIZED.wait()
    payload = _existing_session()
    httpd.shutdown()
    if not payload:
        set_state(status="error", error="no session after login")
        raise SystemExit(1)
    os.environ["TELEGRAM_SESSION_STRING"] = payload
    print("Authorized; starting MCP server.", flush=True)
    _start_mcp()


if __name__ == "__main__":
    main()
