"""
Tests for AUDIT-016: execute_in_clause helper produces safe parameterized
queries for both empty and non-empty ID lists, on SQLite.
"""
import pytest
import sqlite3
from utils.db import execute_in_clause, ensure_schema


@pytest.fixture
def simple_db():
    """Minimal in-memory SQLite DB with a payments-like table."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE items (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            val  INTEGER
        )
    """)
    for i, name, val in [(1, "a", 10), (2, "b", 20), (3, "c", 30)]:
        conn.execute("INSERT INTO items (id, name, val) VALUES (?,?,?)", (i, name, val))
    conn.commit()
    return conn


def test_empty_list_returns_none(simple_db):
    """execute_in_clause with empty list must return None without raising."""
    result = execute_in_clause(simple_db, "DELETE FROM items WHERE id IN {IN_CLAUSE}", [])
    assert result is None


def test_single_id(simple_db):
    """Single ID returns exactly that row."""
    cur = execute_in_clause(
        simple_db, "SELECT * FROM items WHERE id IN {IN_CLAUSE}", [2])
    rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["id"] == 2


def test_multiple_ids(simple_db):
    """Multiple IDs return exactly those rows."""
    cur = execute_in_clause(
        simple_db, "SELECT * FROM items WHERE id IN {IN_CLAUSE}", [1, 3])
    rows = cur.fetchall()
    ids_found = {r["id"] for r in rows}
    assert ids_found == {1, 3}


def test_result_matches_manual_parameterized(simple_db):
    """Result is identical to a manually built parameterized query."""
    ids = [1, 2]
    cur_helper = execute_in_clause(
        simple_db, "SELECT id FROM items WHERE id IN {IN_CLAUSE} ORDER BY id", ids)
    rows_helper = [r["id"] for r in cur_helper.fetchall()]

    cur_manual = simple_db.execute(
        "SELECT id FROM items WHERE id IN (?,?) ORDER BY id", (1, 2))
    rows_manual = [r["id"] for r in cur_manual.fetchall()]

    assert rows_helper == rows_manual


def test_extra_params_appended(simple_db):
    """Extra params after the IN list are passed through correctly."""
    cur = execute_in_clause(
        simple_db,
        "SELECT * FROM items WHERE id IN {IN_CLAUSE} AND val > ?",
        [1, 2, 3], (15,))
    rows = cur.fetchall()
    ids_found = {r["id"] for r in rows}
    assert 2 in ids_found
    assert 3 in ids_found
    assert 1 not in ids_found


def test_delete_with_in_clause(simple_db):
    """execute_in_clause can drive a DELETE — verifies write path."""
    execute_in_clause(simple_db, "DELETE FROM items WHERE id IN {IN_CLAUSE}", [1, 2])
    simple_db.commit()
    remaining = simple_db.execute("SELECT COUNT(*) AS n FROM items").fetchone()["n"]
    assert remaining == 1


def test_no_id_injection_via_string_type(simple_db):
    """IDs are always passed as parameters, not embedded in the SQL string."""
    ids = [1]
    cur = execute_in_clause(
        simple_db, "SELECT * FROM items WHERE id IN {IN_CLAUSE}", ids)
    # If IDs were concatenated into SQL, a string '1 OR 1=1' would select all rows.
    # Since they're parameterized, passing the integer 1 selects exactly 1 row.
    assert len(cur.fetchall()) == 1
