import pytest
from utils.formatters import fmt_inr, parse_slash_amount, h


# ── fmt_inr ────────────────────────────────────────────────────

def test_fmt_inr_four_digits():
    assert fmt_inr(1234) == "₹ 1,234.00"


def test_fmt_inr_six_digits():
    assert fmt_inr(123456) == "₹ 1,23,456.00"


def test_fmt_inr_seven_digits():
    assert fmt_inr(1234567) == "₹ 12,34,567.00"


def test_fmt_inr_zero():
    assert fmt_inr(0) == "₹ 0.00"


def test_fmt_inr_negative():
    result = fmt_inr(-5000)
    assert "5,000.00" in result
    assert "−" in result  # Unicode minus sign


def test_fmt_inr_none():
    assert fmt_inr(None) == "₹ –"  # "₹ –"


# ── parse_slash_amount ─────────────────────────────────────────

def test_parse_slash_normal():
    assert parse_slash_amount("1500") == 1500.0


def test_parse_slash_decimal():
    assert parse_slash_amount("1500/50") == 1500.50


def test_parse_slash_rejects_scientific():
    with pytest.raises(ValueError, match="Scientific"):
        parse_slash_amount("1e5")


def test_parse_slash_rejects_empty():
    with pytest.raises(ValueError):
        parse_slash_amount("")


def test_parse_slash_rejects_none():
    with pytest.raises((ValueError, AttributeError, TypeError)):
        parse_slash_amount(None)


def test_parse_slash_rejects_multiple_slashes():
    with pytest.raises(ValueError):
        parse_slash_amount("1/2/3")


def test_parse_slash_comma_stripped():
    assert parse_slash_amount("1,00,000") == 100000.0


# ── h() XSS escaping ──────────────────────────────────────────

def test_h_escapes_script_tag():
    result = h("<script>alert(1)</script>")
    assert "<script>" not in result
    assert "&lt;script&gt;" in result


def test_h_escapes_quote():
    result = h('"hello"')
    assert '"hello"' not in result


def test_h_safe_string_unchanged():
    result = h("Ramesh Kumar")
    assert result == "Ramesh Kumar"
