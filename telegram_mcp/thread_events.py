"""Persist metadata for a configured Telegram reply subscription."""

import asyncio
import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from telethon import events

READ_LIMIT = 100
WAIT_SECONDS = 25


@dataclass(frozen=True)
class ThreadSubscription:
    account: str
    chat_id: int
    thread_id: int
    sender_id: int

    @property
    def key(self) -> str:
        return f"{self.account}:{self.chat_id}:{self.thread_id}:{self.sender_id}"

    def matches(self, event) -> bool:
        reply = event.message.reply_to
        thread = (reply.reply_to_top_id or reply.reply_to_msg_id) if reply else None
        return (event.chat_id, thread, event.sender_id) == (
            self.chat_id,
            self.thread_id,
            self.sender_id,
        )


class ThreadOutbox:
    def __init__(self, path: Path, subscription: ThreadSubscription):
        self.path = path
        self.subscription = subscription
        self.changed = asyncio.Event()
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS thread_events (
                seq INTEGER PRIMARY KEY, subscription TEXT NOT NULL, message_id INTEGER NOT NULL,
                UNIQUE(subscription, message_id))""")

    async def record(self, event) -> None:
        if not self.subscription.matches(event):
            return
        await asyncio.to_thread(self._persist_message, event.message.id)
        previous = self.changed
        self.changed = asyncio.Event()
        previous.set()

    async def wait_after(self, cursor: int) -> dict:
        changed = self.changed
        page = await asyncio.to_thread(self.read_after, cursor)
        if page["events"]:
            return page
        try:
            await asyncio.wait_for(changed.wait(), timeout=WAIT_SECONDS)
        except asyncio.TimeoutError:
            pass
        return await asyncio.to_thread(self.read_after, cursor)

    def _persist_message(self, message_id: int) -> None:
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "INSERT INTO thread_events (subscription, message_id) VALUES (?, ?) "
                "ON CONFLICT(subscription, message_id) DO NOTHING",
                (self.subscription.key, message_id),
            )

    def read_after(self, cursor: int) -> dict:
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute(
                "SELECT seq, message_id FROM thread_events WHERE subscription = ? AND seq > ? "
                "ORDER BY seq LIMIT ?",
                (self.subscription.key, cursor, READ_LIMIT),
            ).fetchall()
        return {
            "subscription": self.subscription.key,
            "cursor": rows[-1][0] if rows else cursor,
            "events": [{"seq": seq, "message_id": message_id} for seq, message_id in rows],
        }


def configure_thread_outbox(clients, is_chat_allowed) -> ThreadOutbox | None:
    target = os.getenv("TELEGRAM_THREAD_EVENTS_TARGET")
    if not target:
        return None
    if not os.getenv("MCP_AUTH_TOKEN"):
        raise ValueError("Thread events require bearer authentication")
    account, chat, thread, sender = target.split(":")
    subscription = ThreadSubscription(account, int(chat), int(thread), int(sender))
    if subscription.chat_id >= 0 or min(subscription.thread_id, subscription.sender_id) <= 0:
        raise ValueError("Thread events require a group, thread and sender")
    if not is_chat_allowed(subscription.chat_id):
        raise ValueError("Thread events target is outside the chat allowlist")
    client = clients[account]
    outbox = ThreadOutbox(Path(os.environ["TELEGRAM_THREAD_EVENTS_DB"]), subscription)
    client.add_event_handler(
        outbox.record, events.NewMessage(incoming=True, chats=[subscription.chat_id])
    )
    return outbox
