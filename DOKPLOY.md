# Dokploy deployment

This fork runs `telegram-mcp` as a remote MCP Streamable HTTP server for Dokploy.

## Endpoint

- Health check: `GET /healthz`
- MCP endpoint: `/mcp`

## Required environment variables

Set these in Dokploy as secrets/env vars:

```env
TELEGRAM_API_ID=<from my.telegram.org/apps>
TELEGRAM_API_HASH=<from my.telegram.org/apps>
TELEGRAM_SESSION_STRING=<Telethon StringSession>
TELEGRAM_EXPOSED_TOOLS=all
MCP_AUTH_TOKEN=<long random bearer token>
PORT=8000
HOST=0.0.0.0
MCP_PATH=/mcp
MCP_ALLOWED_HOSTS=telegram-mcp.dokploy.windbit.dev,127.0.0.1,localhost
```

`TELEGRAM_EXPOSED_TOOLS=all` exposes write-capable Telegram MCP tools such as sending, editing, deleting, pinning, and reacting to messages. Use `read-only` only for restricted rollouts where MCP clients must not mutate Telegram state.

## Thread event subscription

Set `TELEGRAM_THREAD_EVENTS_TARGET` to `account:chat_id:thread_id:sender_id` and `TELEGRAM_THREAD_EVENTS_DB` to a SQLite path on a persistent volume. The HTTP runner requires `MCP_AUTH_TOKEN` and checks the configured chat against its allowlist. Without a target this feature is off.

Authorized consumers call `GET /thread-events?after=0` with the MCP Bearer header. The request waits up to 25 seconds and returns at most 100 events with a cursor. Save the cursor after handling the page. The database stores message IDs, not text, and deduplicates deliveries for the same subscription. Keep the database across restarts. The runner does not recover Telegram messages it missed while offline; this endpoint replays only events it already stored.

## Hermes remote MCP config

```yaml
mcp_servers:
  telegram:
    url: "https://<dokploy-domain>/mcp"
    headers:
      Authorization: "Bearer <MCP_AUTH_TOKEN>"
    timeout: 120
    connect_timeout: 60
```

Restart Hermes after adding the server.

## Notes

- The upstream project defaults to stdio MCP. This fork adds `telegram_mcp.runner_http`, which connects the Telegram client(s), warms dialog caches, and serves the same FastMCP server over Streamable HTTP.
- `/healthz` and `/` are public for platform health checks. `/mcp` requires `Authorization: Bearer $MCP_AUTH_TOKEN` when `MCP_AUTH_TOKEN` is set.
- Do not put Telegram session strings or API hashes in git.
