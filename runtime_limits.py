"""Small, process-wide limits for the 512 MiB web service."""

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from functools import wraps
import sys
import threading
import time


def _retained_size(value, seen=None):
    """Estimate retained Python memory, including nested JSON containers."""
    seen = set() if seen is None else seen
    identity = id(value)
    if identity in seen:
        return 0
    seen.add(identity)
    size = sys.getsizeof(value)
    if isinstance(value, dict):
        size += sum(_retained_size(k, seen) + _retained_size(v, seen) for k, v in value.items())
    elif isinstance(value, (list, tuple, set)):
        size += sum(_retained_size(item, seen) for item in value)
    return size


class BoundedTTLCache:
    """Thread-safe LRU with both byte and entry limits; oversized values bypass it."""

    def __init__(self, max_bytes, max_entries, ttl, clock=time.monotonic):
        self.max_bytes = max_bytes
        self.max_entries = max_entries
        self.ttl = ttl
        self.clock = clock
        self._entries = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()

    def _remove(self, key):
        entry = self._entries.pop(key)
        self._bytes -= entry[2]

    def _expire(self, now):
        for key, (stored_at, _, _) in list(self._entries.items()):
            if now - stored_at >= self.ttl:
                self._remove(key)

    def get(self, key, ttl=None):
        with self._lock:
            now = self.clock()
            self._expire(now)
            entry = self._entries.get(key)
            if entry is None:
                return None
            if now - entry[0] >= (self.ttl if ttl is None else ttl):
                self._remove(key)
                return None
            self._entries.move_to_end(key)
            return entry[1]

    def set(self, key, value):
        size = _retained_size(key) + _retained_size(value)
        with self._lock:
            now = self.clock()
            self._expire(now)
            if key in self._entries:
                self._remove(key)
            if size > self.max_bytes:
                return value
            while self._entries and (self._bytes + size > self.max_bytes or len(self._entries) >= self.max_entries):
                self._remove(next(iter(self._entries)))
            self._entries[key] = (now, value, size)
            self._bytes += size
        return value

    def clear(self):
        with self._lock:
            self._entries.clear()
            self._bytes = 0

    def stats(self):
        with self._lock:
            self._expire(self.clock())
            return {"entries": len(self._entries), "estimated_bytes": self._bytes,
                    "max_bytes": self.max_bytes, "max_entries": self.max_entries}


class BoundedSourcePool:
    """Never queue new Yahoo workers behind workers that outlived a request deadline."""

    def __init__(self, workers=3):
        self._slots = threading.BoundedSemaphore(workers)
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="sethistock-core")

    def submit(self, function):
        if not self._slots.acquire(blocking=False):
            return None
        try:
            future = self._executor.submit(function)
        except BaseException:
            self._slots.release()
            raise
        future.add_done_callback(lambda _: self._slots.release())
        return future


_FILING_PARSE_LOCK = threading.RLock()


def serialise_filing_parse(function):
    """A filing's temporary BeautifulSoup tree can dwarf its JSON result."""
    @wraps(function)
    def guarded(*args, **kwargs):
        with _FILING_PARSE_LOCK:
            return function(*args, **kwargs)
    return guarded
