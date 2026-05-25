"""
AUDIT-004 meta-test: prevent regression of bare `except: pass` patterns
in pages/ (where they would silently swallow DB and business logic errors).

utils/ exceptions are excluded — some are intentional best-effort fallbacks
(pool cleanup, streamlit import fallback, cache invalidation) marked with
comments in the source. This test focuses on the page layer where silent
failures are most harmful.
"""
import os
import re
import ast


_PAGES_DIR = os.path.join(os.path.dirname(__file__), "..", "pages")

# Matches:
#   except:
#   except Exception:         (no "as e")
#   except Exception as e:    followed only by "pass"
_BARE_EXCEPT_RE = re.compile(
    r"""
    ^\s*except\s*(?:Exception)?\s*(?:as\s+\w+)?\s*:\s*(?:\#.*)?$   # except line
    \n
    \s*pass\s*(?:\#.*)?$                                             # next line is just pass
    """,
    re.MULTILINE | re.VERBOSE,
)


def _find_bare_excepts_in_file(filepath: str) -> list[tuple[int, str]]:
    """Return list of (line_number, context_snippet) for bare except:pass blocks."""
    with open(filepath, encoding="utf-8") as fh:
        source = fh.read()
    hits = []
    for m in _BARE_EXCEPT_RE.finditer(source):
        line_no = source[: m.start()].count("\n") + 1
        hits.append((line_no, m.group(0).strip()))
    return hits


def _all_page_files():
    return [
        os.path.join(_PAGES_DIR, f)
        for f in os.listdir(_PAGES_DIR)
        if f.endswith(".py")
    ]


def test_no_bare_except_pass_in_pages():
    """No page file may contain an unlogged `except: pass` or `except Exception: pass`."""
    violations = []
    for filepath in _all_page_files():
        hits = _find_bare_excepts_in_file(filepath)
        for line_no, snippet in hits:
            violations.append(
                f"{os.path.basename(filepath)}:{line_no} → {snippet!r}"
            )

    assert not violations, (
        "Bare except:pass found in pages/ (swallows errors silently):\n"
        + "\n".join(f"  {v}" for v in violations)
    )


def test_utils_calculator_no_bare_excepts():
    """calculator.py must not have bare except:pass (it only has fallback-value patterns)."""
    path = os.path.join(os.path.dirname(__file__), "..", "utils", "calculator.py")
    hits = _find_bare_excepts_in_file(path)
    assert not hits, f"Bare except:pass in calculator.py: {hits}"


def test_utils_formatters_no_bare_excepts():
    """formatters.py must not have bare except:pass."""
    path = os.path.join(os.path.dirname(__file__), "..", "utils", "formatters.py")
    hits = _find_bare_excepts_in_file(path)
    assert not hits, f"Bare except:pass in formatters.py: {hits}"


def test_utils_passbook_helpers_no_bare_excepts():
    """passbook_helpers.py must not have bare except:pass."""
    path = os.path.join(
        os.path.dirname(__file__), "..", "utils", "passbook_helpers.py"
    )
    if not os.path.exists(path):
        return  # file may not exist in all environments
    hits = _find_bare_excepts_in_file(path)
    assert not hits, f"Bare except:pass in passbook_helpers.py: {hits}"
