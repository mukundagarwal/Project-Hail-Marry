"""
Meta-test: production page and utility files must not contain SQLite-specific
SQL patterns. These patterns are only permitted inside utils/db.py's
_sqlite_ddl() function, which exists solely to set up the test environment.
"""
import re
import os
import pytest


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Files that contain SQL queries — checked for dialect-specific patterns.
# Pure Python utility files (formatters.py, styles.py) are excluded because
# they contain no SQL and their Python methods would cause false positives.
_PRODUCTION_FILES = [
    os.path.join(_PROJECT_ROOT, "pages", "1_Customer_Payments.py"),
    os.path.join(_PROJECT_ROOT, "pages", "2_Vendor_Payments.py"),
    os.path.join(_PROJECT_ROOT, "pages", "3_Stock_Register.py"),
    os.path.join(_PROJECT_ROOT, "pages", "4_Passbook.py"),
    os.path.join(_PROJECT_ROOT, "utils", "calculator.py"),
    os.path.join(_PROJECT_ROOT, "utils", "passbook_helpers.py"),
    os.path.join(_PROJECT_ROOT, "utils", "auth.py"),
    os.path.join(_PROJECT_ROOT, "app.py"),
]

# Patterns matched against SQL string content in production files.
# All are anchored to SQL-context usage (single-quoted arguments, SQL keywords).
# Python method calls like `.strftime(...)` or `datetime(2026, 1, 1)` are NOT matched.
_SQLITE_PATTERNS = [
    # SQLite datetime functions with 'now' argument — SQL context only
    r"datetime\s*\(\s*['\"]now",           # datetime('now', ...) in SQL
    r"date\s*\(\s*['\"]now",               # date('now') in SQL
    r"julianday\s*\(",                     # julianday(...) — SQLite only
    r"strftime\s*\(['\"][^'\"]+['\"].*['\"]now",  # strftime('fmt', 'now') in SQL
    # PostgreSQL-specific — also disallowed (cross-dialect)
    r"to_char\s*\(",                       # to_char(...) — PostgreSQL only
    r"INTERVAL\s+'",                       # PostgreSQL INTERVAL literal
]


def _check_file(filepath: str, patterns: list) -> list:
    """Return list of (line_number, line, pattern) tuples for any hit."""
    hits = []
    with open(filepath, encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            for pat in patterns:
                if re.search(pat, line, re.IGNORECASE):
                    hits.append((lineno, line.rstrip(), pat))
    return hits


@pytest.mark.parametrize("filepath", _PRODUCTION_FILES)
def test_no_sqlite_specific_sql_in_production_file(filepath):
    """No SQLite-specific or PostgreSQL-specific SQL patterns in production code."""
    hits = _check_file(filepath, _SQLITE_PATTERNS)
    if hits:
        report = "\n".join(
            f"  line {ln}: {line!r}  [pattern: {pat}]"
            for ln, line, pat in hits
        )
        pytest.fail(
            f"SQLite/dialect-specific SQL found in {os.path.basename(filepath)}:\n{report}"
        )


def test_db_py_sqlite_patterns_confined_to_sqlite_ddl():
    """
    utils/db.py may use SQLite-specific patterns, but only inside _sqlite_ddl().
    Patterns found OUTSIDE that function are a bug.
    """
    db_path = os.path.join(_PROJECT_ROOT, "utils", "db.py")
    with open(db_path, encoding="utf-8") as f:
        lines = f.readlines()

    in_sqlite_ddl = False
    sqlite_ddl_indent = None
    violations = []

    for lineno, line in enumerate(lines, start=1):
        stripped = line.lstrip()

        # Detect entry into _sqlite_ddl function
        if re.match(r"def _sqlite_ddl\s*\(", stripped):
            in_sqlite_ddl = True
            sqlite_ddl_indent = len(line) - len(stripped)
            continue

        # Detect exit from _sqlite_ddl (next def at same or lower indent)
        if in_sqlite_ddl:
            if stripped and re.match(r"def ", stripped):
                current_indent = len(line) - len(stripped)
                if current_indent <= sqlite_ddl_indent:
                    in_sqlite_ddl = False

        if in_sqlite_ddl:
            continue  # patterns inside _sqlite_ddl are allowed

        # Check for disallowed patterns outside _sqlite_ddl
        disallowed = [r"to_char\s*\(", r"INTERVAL\s+'"]
        for pat in disallowed:
            if re.search(pat, line, re.IGNORECASE):
                violations.append((lineno, line.rstrip(), pat))

    if violations:
        report = "\n".join(
            f"  line {ln}: {line!r}  [pattern: {pat}]"
            for ln, line, pat in violations
        )
        pytest.fail(f"Dialect-specific SQL outside _sqlite_ddl in db.py:\n{report}")
