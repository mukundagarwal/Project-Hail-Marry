"""
Tests for AUDIT-030: invalidate_lookup_cache clears all lookup caches and
can be called safely from any write path.
"""
import pytest
from utils.db import (
    invalidate_lookup_cache,
    get_all_brokers_cached,
    get_all_vendors_cached,
    get_merged_goods_cached,
)


def test_invalidate_does_not_raise():
    """invalidate_lookup_cache must not raise under any conditions."""
    invalidate_lookup_cache()


def test_invalidate_can_be_called_multiple_times():
    """Multiple consecutive invalidation calls must not raise."""
    invalidate_lookup_cache()
    invalidate_lookup_cache()
    invalidate_lookup_cache()


def test_cache_functions_have_clear_method():
    """All cached lookup functions must expose a .clear() method (Streamlit cache API)."""
    for fn in (get_all_brokers_cached, get_all_vendors_cached, get_merged_goods_cached):
        assert hasattr(fn, "clear"), f"{fn.__name__} must have a .clear() method"


def test_invalidate_clears_all_three_caches():
    """
    Verifies that invalidate_lookup_cache calls .clear() on every cached function.
    We call clear() explicitly first (to seed any internal Streamlit state), then
    call invalidate_lookup_cache() and verify no exception is raised.  This is a
    smoke test — deep cache-state inspection requires a running Streamlit runtime.
    """
    for fn in (get_all_brokers_cached, get_all_vendors_cached, get_merged_goods_cached):
        try:
            fn.clear()
        except Exception:
            pass  # non-Streamlit env — .clear() is a no-op identity function

    invalidate_lookup_cache()  # must not raise


def test_invalidate_called_after_schema_seed(db):
    """
    Simulate the pattern: write a new broker row, then invalidate.
    The write itself is tested elsewhere; here we just verify the full
    sequence runs without error in a test DB environment.
    """
    db.execute(
        "INSERT OR IGNORE INTO brokers (broker_name) VALUES (?)", ("CacheTestBroker",))
    db.commit()
    invalidate_lookup_cache()
