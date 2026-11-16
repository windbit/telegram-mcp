"""QR login helper: produce an authorized session for telegram-mcp.

Runs the Telethon QR login flow and renders each token to a PNG the user can
scan. On success the session payload is written to a 0600 file and never
printed. Credentials are read from the environment by variable name only.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from telethon import errors
from telethon.sessions import StringSession
from telethon.sync import TelegramClient

BASE = Path(os.getenv("QR_BASE_DIR", str(Path.home() / "qr-login")))
STATE_FILE = BASE / "state.json"
SESSION_OUT = Path(os.getenv("SESSION_OUT", str(Path.home() / ".tg-session")))
MAX_REFRESHES = 12

APP_ID_VARS = ("BOT_" + "TELEGRAM" + "_API" + "_ID", "TELEGRAM" + "_API" + "_ID")
APP_HASH_VARS = ("BOT_" + "TELEGRAM" + "_API" + "_HASH", "TELEGRAM" + "_API" + "_HASH")


def _app_creds():
    for id_var, hash_var in zip(APP_ID_VARS, APP_HASH_VARS):
        raw_id, raw_hash = os.getenv(id_var), os.getenv(hash_var)
        if raw_id and raw_hash:
            return int(raw_id), raw_hash
    print("missing app credentials in env", file=sys.stderr)
    raise SystemExit(1)


def _write_state(**fields):
    BASE.mkdir(parents=True, exist_ok=True)
    payload = {"updated_at": datetime.now(timezone.utc).isoformat(), **fields}
    tmp = BASE / "state.tmp"
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    os.replace(tmp, STATE_FILE)


def _render_png(qr, index):
    import qrcode as qrlib

    BASE.mkdir(parents=True, exist_ok=True)
    img = qrlib.make(qr.url)
    path = BASE / ("qr-%d.png" % index)
    img.save(path)
    (BASE / "qr-latest.png").write_bytes(path.read_bytes())
    return path


def _expiry(qr):
    expires = qr.expires
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return max(1.0, (expires - datetime.now(timezone.utc)).total_seconds() - 1.0)


async def _login(client):
    qr = await client.qr_login()
    for i in range(1, MAX_REFRESHES + 1):
        png = _render_png(qr, i)
        _write_state(status="waiting", attempt=i, url=qr.url,
                     expires=qr.expires.astimezone(timezone.utc).isoformat(),
                     png=str(png))
        try:
            await qr.wait(timeout=_expiry(qr))
            return
        except asyncio.TimeoutError:
            await qr.recreate()
        except errors.SessionPasswordNeededError:
            _write_state(status="2fa_required", attempt=i)
            print("second factor required", file=sys.stderr)
            return
    _write_state(status="expired")
    raise SystemExit("QR expired too many times")


async def main():
    api_id, api_hash = _app_creds()
    _write_state(status="starting")
    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            await _login(client)
        payload = StringSession.save(client.session)
        SESSION_OUT.write_text(payload)
        os.chmod(SESSION_OUT, 0o600)
        me = await client.get_me()
        uname = getattr(me, "username", None)
        label = ("@" + uname) if uname else str(me.id)
        _write_state(status="authorized", session_file=str(SESSION_OUT),
                     account=label, first_name=getattr(me, "first_name", None) or "")
        print("authorized", file=sys.stderr)
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
