import asyncio
from types import SimpleNamespace

import pytest
from mcp.server.fastmcp import FastMCP
from starlette.testclient import TestClient

from telegram_mcp import runner_http
from telegram_mcp.thread_events import ThreadOutbox, ThreadSubscription, configure_thread_outbox


@pytest.fixture
def isolated_mcp(monkeypatch):
    monkeypatch.setattr(runner_http._runtime, "mcp", FastMCP("thread-event-test"))


@pytest.mark.asyncio
async def test_only_matching_replies_survive_restart_without_duplicates(tmp_path):
    path = tmp_path / "events.sqlite3"
    subscription = ThreadSubscription("support", -100123, 10, 20)
    outbox = ThreadOutbox(path, subscription)
    cases = [
        (-100123, 10, 20, 1),
        (-100123, 10, 20, 1),
        (-100124, 10, 20, 2),
        (-100123, 11, 20, 3),
        (-100123, 10, 21, 4),
    ]
    for chat, thread, sender, message_id in cases:
        reply = SimpleNamespace(reply_to_top_id=None, reply_to_msg_id=thread)
        message = SimpleNamespace(
            id=message_id, reply_to=reply, text="not persisted", chat_id=chat, sender_id=sender
        )
        await outbox.record(SimpleNamespace(chat_id=chat, sender_id=sender, message=message))
    reopened = ThreadOutbox(path, subscription)
    page = reopened.read_after(0)
    assert page == {
        "subscription": subscription.key,
        "cursor": 1,
        "events": [{"seq": 1, "message_id": 1}],
    }
    assert reopened.read_after(page["cursor"])["events"] == []


@pytest.mark.asyncio
async def test_wait_returns_after_matching_reply_is_saved(tmp_path):
    outbox = ThreadOutbox(
        tmp_path / "events.sqlite3", ThreadSubscription("support", -100123, 10, 20)
    )
    reply = SimpleNamespace(reply_to_top_id=10, reply_to_msg_id=11)
    message = SimpleNamespace(id=12, reply_to=reply, chat_id=-100123, sender_id=20)
    waiter = asyncio.create_task(outbox.wait_after(0))
    await asyncio.sleep(0)
    await outbox.record(SimpleNamespace(chat_id=-100123, sender_id=20, message=message))
    page = await asyncio.wait_for(waiter, timeout=2)
    assert page["events"] == [{"seq": 1, "message_id": 12}]


def test_http_event_reader_rejects_anonymous_requests(tmp_path, monkeypatch, isolated_mcp):
    monkeypatch.setenv("MCP_AUTH_TOKEN", "test-bearer")
    outbox = ThreadOutbox(
        tmp_path / "events.sqlite3", ThreadSubscription("support", -100123, 10, 20)
    )
    with TestClient(runner_http._build_app(outbox)) as client:
        assert client.get("/thread-events?after=-1").status_code == 401
        response = client.get(
            "/thread-events?after=-1", headers={"Authorization": "Bearer test-bearer"}
        )
        assert response.status_code == 400


def test_http_reader_replays_saved_events(tmp_path, monkeypatch, isolated_mcp):
    monkeypatch.setenv("MCP_AUTH_TOKEN", "test-bearer")
    target = ThreadSubscription("support", -100123, 10, 20)
    path = tmp_path / "events.sqlite3"
    outbox = ThreadOutbox(path, target)
    reply = SimpleNamespace(reply_to_top_id=None, reply_to_msg_id=10)
    message = SimpleNamespace(id=12, reply_to=reply, chat_id=-100123, sender_id=20)
    asyncio.run(outbox.record(SimpleNamespace(chat_id=-100123, sender_id=20, message=message)))
    with TestClient(runner_http._build_app(ThreadOutbox(path, target))) as client:
        response = client.get(
            "/thread-events?after=0", headers={"Authorization": "Bearer test-bearer"}
        )
    assert (response.status_code, response.json()) == (
        200,
        {"subscription": target.key, "cursor": 1, "events": [{"seq": 1, "message_id": 12}]},
    )


def test_subscription_cannot_start_without_authentication(monkeypatch):
    monkeypatch.setenv("TELEGRAM_THREAD_EVENTS_TARGET", "support:-100123:10:20")
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    with pytest.raises(ValueError, match="bearer authentication"):
        configure_thread_outbox({}, lambda _: True)


@pytest.mark.asyncio
async def test_startup_recovers_replies_missing_from_outbox(tmp_path):
    path = tmp_path / "events.sqlite3"
    target = ThreadSubscription("support", -100123, 10, 20)
    reply = SimpleNamespace(reply_to_top_id=None, reply_to_msg_id=10)
    message = SimpleNamespace(id=12, reply_to=reply, chat_id=target.chat_id, sender_id=20)

    class TelegramHistory:
        async def iter_messages(self, chat_id, *, reply_to, **kwargs):
            assert (chat_id, reply_to) == (target.chat_id, target.thread_id)
            yield message

    await ThreadOutbox(path, target).recover(TelegramHistory())
    reopened = ThreadOutbox(path, target)
    await reopened.recover(TelegramHistory())
    assert reopened.read_after(0)["events"] == [{"seq": 1, "message_id": 12}]
