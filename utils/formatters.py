"""
Formatting and parsing helpers shared across all pages.
No Streamlit imports here — pure Python only.
"""

import html as _html
import logging
from datetime import datetime, date
from decimal import Decimal, InvalidOperation

_logger = logging.getLogger(__name__)


def h(s: object) -> str:
    """HTML-escape a value for safe insertion into markup."""
    return _html.escape(str(s) if s is not None else "", quote=True)


def fmt_date(d) -> str:
    """Format a date-like value as DD/MM/YYYY. Returns '–' for None."""
    if d is None:
        return "–"
    if isinstance(d, str):
        try:
            d = datetime.strptime(d, "%Y-%m-%d").date()
        except Exception:
            return str(d)
    return d.strftime("%d/%m/%Y")


def parse_slash_amount(raw: str) -> float:
    """Accept '1234', '12.50', or '12/50' (slash as decimal separator)."""
    if raw is None or str(raw).strip() == "":
        raise ValueError("Empty input")
    raw = str(raw).strip().replace(",", "")
    if raw.count("/") == 1:
        raw = raw.replace("/", ".")
    elif raw.count("/") > 1:
        raise ValueError(f"Too many '/' in: {raw}")
    if 'e' in raw.lower():
        raise ValueError("Scientific notation not accepted")
    try:
        return float(Decimal(raw))
    except Exception:
        raise ValueError(f"Cannot parse amount: '{raw}'")


def fmt_inr(amount) -> str:
    """Format a number as Indian Rupees with lakh/crore grouping."""
    try:
        v = float(amount)
    except (TypeError, ValueError):
        return "₹ –"
    neg = v < 0
    s = f"{abs(v):.2f}"
    integer, decimal_part = s.split(".")
    if len(integer) <= 3:
        grouped = integer
    else:
        grouped = integer[-3:]
        integer = integer[:-3]
        while integer:
            grouped = integer[-2:] + "," + grouped
            integer = integer[:-2]
    return ("−₹ " if neg else "₹ ") + grouped + "." + decimal_part


def days_between(start_str, end_date=None) -> int:
    """Days from start_str (YYYY-MM-DD) to end_date (default: today). Returns 0 on error."""
    try:
        start = datetime.strptime(str(start_str), "%Y-%m-%d").date()
        end   = end_date if isinstance(end_date, date) else date.today()
        return max((end - start).days, 0)
    except Exception as exc:
        _logger.warning(
            "days_between: could not parse date '%s': %s. Returning 0.",
            start_str, exc)
        return 0
