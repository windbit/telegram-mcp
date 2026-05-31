"""HTTP entrypoint for running Telegram MCP behind Dokploy.

The upstream server is primarily a stdio MCP server. Dokploy and other PaaS
runtimes expect a long-running HTTP process, so this module exposes the same
FastMCP server through MCP Streamable HTTP at ``/mcp``.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
from typing import Iterable, Literal, cast

import nest_asyncio
import uvicorn
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route
from mcp.server.transport_security import TransportSecuritySettings

from telegram_mcp import runtime as _runtime
from telegram_mcp.install_guard import UnsafeInstallationError, assert_safe_distribution

try:
    assert_safe_distribution()
except UnsafeInstallationError as exc:  # pragma: no cover - startup guard
    raise SystemExit(str(exc)) from None

from telegram_mcp.runner import _connect_authorized_client  # noqa: E402
import telegram_mcp.tools  # noqa: F401,E402 - registers MCP tools via decorators


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Tiny bearer-token guard for remote MCP deployments.

    Set MCP_AUTH_TOKEN in the container environment. Health endpoints are left
    public so Dokploy can probe the container without credentials.
    """

    def __init__(self, app, token: str, public_paths: Iterable[str] = ("/", "/healthz")):
        super().__init__(app)
        self.token = token
        self.public_paths = set(public_paths)

    async def dispatch(self, request: Request, call_next):
        if request.url.path in self.public_paths:
            return await call_next(request)
        expected = f"Bearer {self.token}"
        if request.headers.get("authorization") != expected:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


def _configure_http_settings() -> None:
    """Apply env-driven HTTP settings to the FastMCP instance."""

    settings = _runtime.mcp.settings
    settings.host = os.getenv("HOST", "0.0.0.0")
    settings.port = int(os.getenv("PORT", "8000"))
    settings.streamable_http_path = os.getenv("MCP_PATH", "/mcp")
    settings.log_level = cast(
        Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        os.getenv("LOG_LEVEL", settings.log_level).upper(),
    )
    settings.stateless_http = os.getenv("MCP_STATELESS_HTTP", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    allowed_hosts = [
        host.strip()
        for host in os.getenv(
            "MCP_ALLOWED_HOSTS",
            "127.0.0.1,localhost,telegram-mcp.dokploy.windbit.dev",
        ).split(",")
        if host.strip()
    ]
    settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
    )


def _build_app():
    app = _runtime.mcp.streamable_http_app()

    async def healthz(request):  # noqa: ARG001
        return JSONResponse(
            {
                "ok": True,
                "service": "telegram-mcp",
                "transport": "streamable-http",
                "path": _runtime.mcp.settings.streamable_http_path,
                "accounts": sorted(_runtime.clients.keys()),
            }
        )

    async def root(request):  # noqa: ARG001
        return PlainTextResponse("telegram-mcp streamable-http endpoint: /mcp\n")

    app.routes.append(Route("/healthz", healthz, methods=["GET"]))
    app.routes.append(Route("/", root, methods=["GET"]))

    token = os.getenv("MCP_AUTH_TOKEN")
    if token:
        app.add_middleware(BearerAuthMiddleware, token=token)
    else:
        print(
            "WARNING: MCP_AUTH_TOKEN is not set; remote MCP endpoint is unauthenticated.",
            file=sys.stderr,
        )
    return app


async def _main_http() -> None:
    try:
        labels = ", ".join(_runtime.clients.keys())
        print(f"Starting {len(_runtime.clients)} Telegram client(s) ({labels})...", file=sys.stderr)
        await asyncio.gather(
            *(
                _connect_authorized_client(label, client)
                for label, client in _runtime.clients.items()
            )
        )

        print("Warming entity caches...", file=sys.stderr)
        await asyncio.gather(*(client.get_dialogs() for client in _runtime.clients.values()))

        _configure_http_settings()
        app = _build_app()
        settings = _runtime.mcp.settings
        print(
            "Telegram client(s) started "
            f"({labels}). Running MCP Streamable HTTP on "
            f"{settings.host}:{settings.port}{settings.streamable_http_path}",
            file=sys.stderr,
        )
        config = uvicorn.Config(
            app,
            host=settings.host,
            port=settings.port,
            log_level=settings.log_level.lower(),
        )
        server = uvicorn.Server(config)
        await server.serve()
    except Exception as exc:
        print(f"Error starting HTTP MCP server: {exc}", file=sys.stderr)
        if isinstance(exc, sqlite3.OperationalError) and "database is locked" in str(exc):
            print(
                "Database lock detected. Please ensure no other instances are using the same file session.",
                file=sys.stderr,
            )
        sys.exit(1)
    finally:
        try:
            await asyncio.gather(
                *(client.disconnect() for client in _runtime.clients.values()),
                return_exceptions=True,
            )
        except Exception:
            pass


def main() -> None:
    _runtime._configure_allowed_roots_from_cli(sys.argv[1:])
    _runtime._apply_exposed_tools_mode()
    nest_asyncio.apply()
    asyncio.run(_main_http())


if __name__ == "__main__":
    main()
