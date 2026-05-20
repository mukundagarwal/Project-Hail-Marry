import pytest
import sqlite3
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from utils.db import ensure_schema


@pytest.fixture
def db():
    """Fresh in-memory SQLite DB with full schema for each test."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_schema(conn=conn)
    yield conn
    conn.close()
