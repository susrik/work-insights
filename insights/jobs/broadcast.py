"""In-process pub/sub carrying the live tail of job logs to SSE clients.

The database is the source of truth (replay and retention); this hub only
delivers events as they happen. Single-process by design.
"""

from __future__ import annotations

import asyncio
import threading
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from typing import Any

Event = dict[str, Any]


class BroadcastHub:
    def __init__(self, buffer_size: int = 5000) -> None:
        self._buffer: dict[int, deque[Event]] = defaultdict(lambda: deque(maxlen=buffer_size))
        self._subs: dict[int, list[asyncio.Queue[Event]]] = defaultdict(list)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def attach(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def publish(self, run_id: int, event: Event) -> None:
        with self._lock:
            self._buffer[run_id].append(event)
            subs = list(self._subs[run_id])
        for queue in subs:
            self._schedule(queue.put_nowait, event)

    def close(self, run_id: int) -> None:
        with self._lock:
            subs = list(self._subs[run_id])
        for queue in subs:
            self._schedule(queue.put_nowait, {"_done": True})

    def _schedule(self, fn: Any, *args: Any) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(fn, *args)

    async def subscribe(self, run_id: int, after_id: int = 0) -> AsyncIterator[Event]:
        """Yield events for ``run_id`` with id > ``after_id`` until the run closes."""
        queue: asyncio.Queue[Event] = asyncio.Queue()
        with self._lock:
            buffered = [e for e in self._buffer[run_id] if e.get("id", 0) > after_id]
            self._subs[run_id].append(queue)
        try:
            for event in buffered:
                yield event
            while True:
                event = await queue.get()
                if event.get("_done"):
                    yield event
                    return
                if event.get("id", 0) <= after_id:
                    continue
                yield event
        finally:
            with self._lock:
                subs = self._subs.get(run_id)
                if subs is not None and queue in subs:
                    subs.remove(queue)


_hub: BroadcastHub | None = None


def set_hub(hub: BroadcastHub | None) -> None:
    global _hub
    _hub = hub


def publish(run_id: int, event: Event) -> None:
    if _hub is not None:
        _hub.publish(run_id, event)


def close(run_id: int) -> None:
    if _hub is not None:
        _hub.close(run_id)
