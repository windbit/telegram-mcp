# Agentek app hosting

Service: `telegram-mcp` (id 170), template «Хостинг приложения» (`git-deploy`).

| | |
|---|---|
| Public URL | https://telegram-mcp-prod-user-148-350-me-mvikharev-ru-7943f908.k.agentek.ru |
| MCP endpoint | `/mcp` (Streamable HTTP) |
| Health | `/healthz` |
| Source | `SOURCE=agent`, `WORKSPACE_DIR=/opt/data/profiles/razrabotchik/workspace`, `BUILD_CONTEXT=telegram-mcp` |
| Port | app `8000` (`APP_PORT=8000`, `EXPOSE 8000`) |

## Why not SOURCE=git

The repository ships a `docker-compose.yml` that mounts `env_file: .env`, which is
not in git. The platform prefers compose over the Dockerfile, so the deploy step
failed with `env file /data/workdir/.env not found`. Agent-source build off the
agent workspace builds straight from the `Dockerfile`; `docker-compose.yml` was
removed in this branch to keep that path unambiguous.

## Required environment

The app calls `int(os.getenv("TELEGRAM_API_ID"))` at import time, so it exits
immediately when these are absent:

- `TELEGRAM_API_ID` — from https://my.telegram.org/apps
- `TELEGRAM_API_HASH` — from https://my.telegram.org/apps
- `TELEGRAM_SESSION_STRING` — Telethon `StringSession` for the account
  (`uv run session_string_generator.py`)
- `MCP_AUTH_TOKEN` — bearer token guarding `/mcp` (`/` and `/healthz` stay public)

Add them as **space secrets** in the console (Секреты → Создать секрет, level
«Агент», name exactly as the env var), then map them via `secret_refs`, or pass
them as static service parameters. `MCP_ALLOWED_HOSTS` must list the generated
host or FastMCP's DNS-rebinding protection rejects requests.

## Verification

`/` is answered by the deployer, not the app: it returns stage JSON
(`{"stage":"building"|"running"|"failed"}`). Confirm the app itself through
`/healthz` (expect `{"ok":true,"service":"telegram-mcp",...}`) and the
application log — a `ValueError: invalid literal for int() with base 10: ''`
there means `TELEGRAM_API_ID` is still missing.

## QR login

The service runs `runner_qr.py`, not the upstream entrypoint:

1. On start it looks for a session (env `TELEGRAM_SESSION_STRING`, else
   `TELEGRAM_SESSION_FILE`, default `/data/session.txt`).
2. Without one it serves `GET /` — a page whose QR image refreshes every ~3 s.
   Telethon QR tokens live ~30 s, so a static image cannot be delivered in time.
   `GET /qr.png` is the current code, `GET /status.json` is the state.
3. After the scan the session is written to `TELEGRAM_SESSION_FILE` (0600) and
   the normal Streamable HTTP MCP server starts on the same port.

Scan path: Telegram → Settings → Devices → Link Desktop Device.

`/data` is the container filesystem: the session does not survive a rebuild, so
after logging in copy the session into a space secret and pass it as
`TELEGRAM_SESSION_STRING` to make it durable.
