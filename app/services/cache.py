import time
from typing import Any, Dict, Optional


class TTLCache:
    """Tiny in-process cache with per-item expiration.

    Serverless runtimes keep a container alive for a short while, so repeated
    lookups of the same tenant/user can be served from memory instead of
    hitting MongoDB on every Telegram update.
    """

    def __init__(self, ttl: float = 30.0, max_items: int = 512) -> None:
        self.ttl = ttl
        self.max_items = max_items
        self._items: Dict[Any, tuple[float, Any]] = {}

    def get(self, key: Any) -> Optional[Any]:
        item = self._items.get(key)
        if item is None:
            return None
        expires_at, value = item
        if expires_at <= time.monotonic():
            self._items.pop(key, None)
            return None
        return value

    def set(self, key: Any, value: Any) -> None:
        if len(self._items) >= self.max_items:
            self._evict()
        self._items[key] = (time.monotonic() + self.ttl, value)

    def delete(self, key: Any) -> None:
        self._items.pop(key, None)

    def clear(self) -> None:
        self._items.clear()

    def _evict(self) -> None:
        now = time.monotonic()
        expired = [key for key, (expires_at, _) in self._items.items() if expires_at <= now]
        for key in expired:
            self._items.pop(key, None)
        if len(self._items) >= self.max_items:
            oldest = min(self._items.items(), key=lambda item: item[1][0])[0]
            self._items.pop(oldest, None)
