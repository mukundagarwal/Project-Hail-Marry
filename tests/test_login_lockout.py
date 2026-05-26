"""
FIX 2: DB-backed login lockout — rate limiting persists across browser sessions.
"""
import pytest
from datetime import datetime, timedelta
from utils.db import record_login_attempt, check_lockout, purge_old_login_attempts


def test_no_lockout_initially(db):
    """Fresh DB: no attempts → not locked."""
    locked, failed = check_lockout("1.2.3.4", max_attempts=5, window_minutes=15, conn=db)
    assert locked is False
    assert failed == 0


def test_lockout_after_max_failed_attempts(db):
    """5 failed attempts from same IP → locked."""
    ip = "10.0.0.1"
    for _ in range(5):
        record_login_attempt(ip, success=False, conn=db)
    db.commit()

    locked, failed = check_lockout(ip, max_attempts=5, window_minutes=15, conn=db)
    assert locked is True
    assert failed == 5


def test_not_locked_before_max(db):
    """4 failed attempts from same IP → not yet locked."""
    ip = "10.0.0.2"
    for _ in range(4):
        record_login_attempt(ip, success=False, conn=db)
    db.commit()

    locked, failed = check_lockout(ip, max_attempts=5, window_minutes=15, conn=db)
    assert locked is False
    assert failed == 4


def test_successful_login_does_not_count_toward_lockout(db):
    """Successful attempts are not counted in the failed-attempts total."""
    ip = "10.0.0.3"
    for _ in range(4):
        record_login_attempt(ip, success=False, conn=db)
    record_login_attempt(ip, success=True, conn=db)
    db.commit()

    locked, failed = check_lockout(ip, max_attempts=5, window_minutes=15, conn=db)
    assert locked is False
    assert failed == 4


def test_different_ips_tracked_separately(db):
    """Lockout for one IP does not affect a different IP."""
    ip_a = "192.168.1.1"
    ip_b = "192.168.1.2"
    for _ in range(5):
        record_login_attempt(ip_a, success=False, conn=db)
    db.commit()

    locked_a, _ = check_lockout(ip_a, max_attempts=5, window_minutes=15, conn=db)
    locked_b, _ = check_lockout(ip_b, max_attempts=5, window_minutes=15, conn=db)
    assert locked_a is True
    assert locked_b is False


def test_lockout_window_excludes_old_attempts(db):
    """
    Attempts older than the window do not count.
    Seed an old failed attempt directly with a past timestamp, then check that
    a fresh attempt within the window does not combine with it to trigger lockout.
    """
    ip = "10.0.0.5"
    old_ts = (datetime.now() - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S")
    # Insert old attempt manually with past timestamp
    db.execute(
        "INSERT INTO login_attempts (ip_address, attempted_at, success) VALUES (?, ?, 0)",
        (ip, old_ts)
    )
    db.commit()

    # Only 1 recent failed attempt (within 15-min window) — not yet locked
    record_login_attempt(ip, success=False, conn=db)
    db.commit()

    locked, failed = check_lockout(ip, max_attempts=5, window_minutes=15, conn=db)
    assert locked is False
    assert failed == 1


def test_purge_removes_old_attempts(db):
    """purge_old_login_attempts deletes records older than `days` days."""
    ip = "172.16.0.1"
    old_ts = (datetime.now() - timedelta(days=8)).strftime("%Y-%m-%dT%H:%M:%S")
    db.execute(
        "INSERT INTO login_attempts (ip_address, attempted_at, success) VALUES (?, ?, 0)",
        (ip, old_ts)
    )
    db.commit()

    cnt_before = db.execute(
        "SELECT COUNT(*) AS cnt FROM login_attempts WHERE ip_address=?", (ip,)
    ).fetchone()["cnt"]
    assert cnt_before == 1

    purge_old_login_attempts(days=7, conn=db)
    db.commit()

    cnt_after = db.execute(
        "SELECT COUNT(*) AS cnt FROM login_attempts WHERE ip_address=?", (ip,)
    ).fetchone()["cnt"]
    assert cnt_after == 0


def test_purge_keeps_recent_attempts(db):
    """purge_old_login_attempts must NOT remove recent records."""
    ip = "172.16.0.2"
    record_login_attempt(ip, success=False, conn=db)
    db.commit()

    purge_old_login_attempts(days=7, conn=db)
    db.commit()

    cnt = db.execute(
        "SELECT COUNT(*) AS cnt FROM login_attempts WHERE ip_address=?", (ip,)
    ).fetchone()["cnt"]
    assert cnt == 1
