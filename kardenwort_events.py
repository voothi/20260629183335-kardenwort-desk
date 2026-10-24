"""Controller-owned queues for browser and native stage delivery."""
import queue
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

class EventDispatcher:
    """
    Thread-safe Server-Sent Events (SSE) dispatcher for session-scoped in-memory streams.
    Manages subscriber queues without requiring disk-based update files.
    """
    _instance: Optional["EventDispatcher"] = None
    _singleton_lock = threading.Lock()

    def __init__(self):
        self.controller_owned = False
        self._polling_clients = {}
        self._subscribers: Dict[str, List[queue.Queue]] = {}
        self._lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "EventDispatcher":
        with cls._singleton_lock:
            if cls._instance is None:
                cls._instance = EventDispatcher()
            return cls._instance

    def register(self, session_zid: str) -> queue.Queue:
        q = queue.Queue(maxsize=1000)
        with self._lock:
            if session_zid not in self._subscribers:
                self._subscribers[session_zid] = []
            self._subscribers[session_zid].append(q)
        return q

    def unregister(self, session_zid: str, q: queue.Queue):
        with self._lock:
            if session_zid in self._subscribers:
                try:
                    self._subscribers[session_zid].remove(q)
                    if not self._subscribers[session_zid]:
                        del self._subscribers[session_zid]
                except ValueError:
                    pass

    def poll_events(self, session_zid, client_id, accept=True, close=False):
        """Expose the same bounded subscriber queues to authenticated native hosts."""
        key = (session_zid, client_id)
        with self._lock:
            self._prune_polling_clients(time.monotonic())
            if close:
                self._remove_polling_client(key)
                return []
            if key not in self._polling_clients:
                channel = queue.Queue(maxsize=1000)
                self._subscribers.setdefault(session_zid, []).append(channel)
                self._polling_clients[key] = (channel, 0)
            channel = self._polling_clients[key][0]
            self._polling_clients[key] = (channel, time.monotonic())
        return self._drain_events(channel) if accept else []

    def _remove_polling_client(self, key):
        subscription = self._polling_clients.pop(key, None)
        if subscription is None:
            return
        channels = self._subscribers.get(key[0], [])
        if subscription[0] in channels:
            channels.remove(subscription[0])
        if not channels:
            self._subscribers.pop(key[0], None)

    def _prune_polling_clients(self, now):
        expired = [key for key, value in self._polling_clients.items() if now - value[1] > 60]
        for key in expired:
            self._remove_polling_client(key)

    @staticmethod
    def _drain_events(channel):
        events = []
        while True:
            try:
                events.append(channel.get_nowait())
            except queue.Empty:
                return events

    def has_subscribers(self, session_zid: str) -> bool:
        with self._lock:
            return bool(self._subscribers.get(session_zid))

    def publish(self, session_zid: str, event_data: Dict[str, Any]):
        payload = dict(event_data)
        if "timestamp" not in payload:
            payload["timestamp"] = datetime.now(timezone.utc).isoformat()
        payload["session_zid"] = session_zid
        with self._lock:
            queues = list(self._subscribers.get(session_zid, []))
        for q in queues:
            try:
                q.put_nowait(payload)
            except queue.Full:
                logger.warning("Subscriber backlog is full for session %s; recover committed state through status", session_zid)
