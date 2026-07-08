import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from telegram_mcp import runner_http
from telegram_mcp import runtime as _runtime


def _client(root, token=None):
    app = Starlette(routes=[Route("/files/{file_path:path}", runner_http._serve_file)])
    if token:
        app.add_middleware(runner_http.BearerAuthMiddleware, token=token)
    return TestClient(app, raise_server_exceptions=True)


def test_serves_file_within_root(tmp_path, monkeypatch):
    (tmp_path / "downloads").mkdir()
    (tmp_path / "downloads" / "a.bin").write_bytes(b"hello")
    monkeypatch.setattr(_runtime, "SERVER_ALLOWED_ROOTS", [tmp_path.resolve()])

    resp = _client(tmp_path).get("/files/downloads/a.bin")
    assert resp.status_code == 200
    assert resp.content == b"hello"


def test_rejects_traversal(tmp_path, monkeypatch):
    outside = tmp_path.parent / "secret.txt"
    outside.write_text("nope")
    monkeypatch.setattr(_runtime, "SERVER_ALLOWED_ROOTS", [tmp_path.resolve()])

    resp = _client(tmp_path).get("/files/../secret.txt")
    assert resp.status_code == 404


def test_missing_file_is_404(tmp_path, monkeypatch):
    monkeypatch.setattr(_runtime, "SERVER_ALLOWED_ROOTS", [tmp_path.resolve()])
    assert _client(tmp_path).get("/files/nope.bin").status_code == 404


def test_disabled_without_roots(tmp_path, monkeypatch):
    monkeypatch.setattr(_runtime, "SERVER_ALLOWED_ROOTS", [])
    assert _client(tmp_path).get("/files/a.bin").status_code == 404


def test_bearer_token_required(tmp_path, monkeypatch):
    (tmp_path / "a.bin").write_bytes(b"x")
    monkeypatch.setattr(_runtime, "SERVER_ALLOWED_ROOTS", [tmp_path.resolve()])
    client = _client(tmp_path, token="s3cret")

    assert client.get("/files/a.bin").status_code == 401
    ok = client.get("/files/a.bin", headers={"authorization": "Bearer s3cret"})
    assert ok.status_code == 200
