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
TELEGRAM_EXPOSED_TOOLS=read-only
MCP_AUTH_TOKEN=<long random bearer token>
PORT=8000
HOST=0.0.0.0
MCP_PATH=/mcp
MCP_ALLOWED_HOSTS=telegram-mcp.dokploy.windbit.dev,127.0.0.1,localhost
```

`TELEGRAM_EXPOSED_TOOLS=read-only` is strongly recommended for the first rollout. Switch to `all` only after confirming the MCP client and access controls behave as expected.

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
