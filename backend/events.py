"""
Server-sent event bus.

Dexter previously had no way to reach the user on its own - the frontend fetched
on mount and after each chat turn, and that was it. Nothing could arrive
unprompted, which is why reminders were impossible.

One SSE channel now carries everything the backend needs to push: streaming
reply tokens, tool progress, fired reminders, and status changes. SSE rather
than WebSocket because the traffic is one-directional and it survives the three
different shells (Electron, WebView2, browser) without extra handling.

publish() is safe to call from the APScheduler background thread; it hops onto
the event loop via call_soon_threadsafe.
"""

import asyncio
import json
import time
from typing import Any, AsyncIterator, Dict, Optional, Set

MAX_QUEUE = 200
HEARTBEAT_SECONDS = 20.0


class EventBus:
    def __init__(self) -> None:
        self._subscribers: Set[asyncio.Queue] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Called once at startup so background threads know where to publish."""
        self._loop = loop

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=MAX_QUEUE)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def _deliver(self, message: Dict[str, Any]) -> None:
        dead = []
        for queue in self._subscribers:
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                # A stalled client must not block the scheduler. Drop it.
                dead.append(queue)
        for queue in dead:
            self._subscribers.discard(queue)

    def publish(self, event_type: str, payload: Any = None) -> None:
        """Broadcast to every connected client. Safe from any thread."""
        message = {"type": event_type, "payload": payload, "ts": time.time()}

        if self._loop is None:
            self._deliver(message)
            return

        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None

        if running is self._loop:
            self._deliver(message)
        else:
            self._loop.call_soon_threadsafe(self._deliver, message)

    async def stream(self, queue: asyncio.Queue) -> AsyncIterator[str]:
        """Yield SSE frames for one client until it disconnects."""
        yield _frame("connected", {"ok": True})
        try:
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    # Comment frame keeps proxies and the browser from timing out.
                    yield ": keepalive\n\n"
                    continue
                yield _frame(message["type"], message["payload"], message["ts"])
        except asyncio.CancelledError:
            raise
        finally:
            self.unsubscribe(queue)


def _frame(event_type: str, payload: Any, ts: Optional[float] = None) -> str:
    body = json.dumps(
        {"type": event_type, "payload": payload, "ts": ts or time.time()},
        ensure_ascii=False,
    )
    return f"event: {event_type}\ndata: {body}\n\n"


bus = EventBus()
