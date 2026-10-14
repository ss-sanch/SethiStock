import threading

from runtime_limits import BoundedSourcePool, BoundedTTLCache


def test_cache_evicts_least_recently_used_and_expires_idle_entries():
    now = [0.0]
    cache = BoundedTTLCache(10000, 2, 10, clock=lambda: now[0])
    cache.set("a", {"value": 1})
    cache.set("b", {"value": 2})
    assert cache.get("a") == {"value": 1}
    cache.set("c", {"value": 3})
    assert cache.get("b") is None
    now[0] = 11
    cache.set("d", {"value": 4})
    assert cache.stats()["entries"] == 1
    assert cache.get("a") is None


def test_cache_bounds_nested_bytes_and_bypasses_oversized_payloads():
    cache = BoundedTTLCache(2000, 10, 60)
    for index in range(100):
        cache.set(str(index), {"rows": [str(index) * 150]})
    assert cache.stats()["estimated_bytes"] <= 2000
    assert cache.stats()["entries"] < 10
    oversized = {"rows": ["x" * 10000]}
    assert cache.set("large", oversized) is oversized
    assert cache.get("large") is None


def test_shorter_namespace_ttl_and_replacement_accounting():
    now = [0.0]
    cache = BoundedTTLCache(10000, 10, 60, clock=lambda: now[0])
    cache.set("facts", "a" * 100)
    cache.set("facts", "a")
    assert cache.stats()["entries"] == 1
    now[0] = 11
    assert cache.get("facts", ttl=10) is None
    assert cache.stats()["estimated_bytes"] == 0


def test_timed_out_workers_keep_their_slots_until_they_finish():
    pool = BoundedSourcePool(workers=1)
    started = threading.Event()
    release = threading.Event()
    def delayed_source():
        started.set()
        release.wait(2)
        return "finished"
    try:
        abandoned = pool.submit(delayed_source)
        assert started.wait(1)
        assert abandoned.cancel() is False
        # Simulates the next request after its predecessor's deadline expired.
        assert pool.submit(lambda: "extra worker") is None
        release.set()
        assert abandoned.result(1) == "finished"
        pool._executor.shutdown(wait=True)
    finally:
        release.set()
        pool._executor.shutdown(wait=True)


def test_worker_errors_release_capacity():
    pool = BoundedSourcePool(workers=1)
    def fail():
        raise ValueError("source failed")
    future = pool.submit(fail)
    try:
        future.result(1)
    except ValueError:
        pass
    second = pool.submit(lambda: "recovered")
    assert second is not None
    assert second.result(1) == "recovered"
    pool._executor.shutdown(wait=True)
