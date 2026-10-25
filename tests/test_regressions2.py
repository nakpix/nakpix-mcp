"""Regression tests for the SECOND review round (non-destructive migration,
immutable ts anchor, confirmation preference, pagination, monthly day-31,
CLI import, email completeness). No money, no network."""

from __future__ import annotations

import datetime
import time

import pytest


# ── 1. non-destructive migration ──────────────────────────────────────────
def test_migrate_preserves_financial_history(isolated_home):
    from nakpix import storage

    storage.init()
    # reviewer scenario: a pending (lower id) + a done (higher id) for the
    # same (schedule_id, due_ts). The old DELETE kept MIN(id) and destroyed the
    # done occurrence with its financial id.
    with storage.conn() as c:
        c.execute(
            "INSERT INTO occurrences (schedule_id, due_ts, status) VALUES (?,?,?)",
            (1, 100, "pending"),
        )
        c.execute(
            "INSERT INTO occurrences (schedule_id, due_ts, status, ordem_id) VALUES (?,?,?,?)",
            (1, 100, "done", "PIX_DONE_123"),
        )
    storage.init()  # re-run migration (must be non-destructive)
    with storage.conn() as c:
        rows = c.execute(
            "SELECT * FROM occurrences WHERE schedule_id=1 AND due_ts=100 ORDER BY id"
        ).fetchall()
    statuses = [r["status"] for r in rows]
    ordens = [r["ordem_id"] for r in rows]
    assert len(rows) == 2                    # nothing deleted
    assert "done" in statuses
    assert "PIX_DONE_123" in ordens          # financial id preserved


# ── 2. immutable ts anchor + updated_at ───────────────────────────────────
def test_record_payment_preserves_ts_anchor(isolated_home):
    from nakpix import storage

    storage.record_payment(
        ordem_id="PX_TS", pix_brl=10.0, chave="a@b.com", network="polygon",
        token="USDT", usdt=2.0, tx_hash="0x1", status="funding",
    )
    ts1 = storage.get_payment("PX_TS")
    assert ts1 is not None
    ts1 = ts1["ts"]
    time.sleep(1.05)  # ensure _now() advances
    storage.record_payment(
        ordem_id="PX_TS", pix_brl=10.0, network="polygon",
        status="sent", resgate="NAK1",
    )
    p = storage.get_payment("PX_TS")
    assert p["ts"] == ts1                    # financial anchor NOT overwritten
    assert p["updated_at"] >= ts1 + 1        # last-write moved
    assert p["status"] == "sent"


# ── 3. pay_pix respects the saved confirmation preference ─────────────────
def test_pay_pix_respects_confirmation_preference(isolated_home):
    from nakpix import server

    server.nakpix_create_wallet("polygon")
    # default: confirmation required
    assert server.nakpix_pay_pix(pix_value=100, chave="a@b.com", confirm=False)["status"] == "blocked"
    # disable confirmation -> guard skipped, reaches key validation
    server.nakpix_set_payment_confirmation(False)
    r = server.nakpix_pay_pix(pix_value=100, chave="****", confirm=False)
    assert r["status"] == "err"              # not "blocked"


# ── 4. monthly recurrence keeps the anchor day (31) ───────────────────────
def test_monthly_recurrence_day31(isolated_home):
    from nakpix import scheduler

    s = {"recurrencia": "monthly", "dia_ancora": 31}
    jan31 = int(datetime.datetime(2026, 1, 31, 12, 0).timestamp())
    feb = scheduler.next_occurrence(s, jan31)
    assert feb is not None
    assert datetime.datetime.fromtimestamp(feb).day == 28   # clamped to Feb
    mar = scheduler.next_occurrence(s, feb)
    assert mar is not None
    assert datetime.datetime.fromtimestamp(mar).day == 31   # back to anchor


def test_monthly_recurrence_no_anchor_uses_day(isolated_home):
    from nakpix import scheduler

    s = {"recurrencia": "monthly"}          # legacy: no dia_ancora
    jan15 = int(datetime.datetime(2026, 1, 15, 12, 0).timestamp())
    feb = scheduler.next_occurrence(s, jan15)
    assert feb is not None
    assert datetime.datetime.fromtimestamp(feb).day == 15


# ── 5. incident monitor pages through ALL unresolved (no cap) ─────────────
def test_list_unresolved_payments_pagination(isolated_home):
    from nakpix import storage

    storage.init()
    now = int(time.time())
    with storage.conn() as c:
        for i in range(3):
            c.execute(
                "INSERT INTO payments (ts, ordem_id, pix_brl, network, tx_hash, status) "
                "VALUES (?,?,?,?,?,?)",
                (now - 1000, f"PX_{i}", 10.0, "polygon", f"0x{i}", "pending"),
            )
    page0 = storage.list_unresolved_payments(now, limit=2, offset=0)
    page1 = storage.list_unresolved_payments(now, limit=2, offset=2)
    assert len(page0) == 2
    assert len(page1) == 1
    ids = {r["ordem_id"] for r in page0 + page1}
    assert ids == {"PX_0", "PX_1", "PX_2"}


# ── 6. CLI import: secret optional (interactive/hidden) ───────────────────
def test_cli_import_no_secret_argument():
    from nakpix.wallet_agent import cli

    args = cli.build_parser().parse_args(["import", "polygon"])
    assert not hasattr(args, "secret")  # secret is NOT a CLI arg anymore
    # a secret passed as an extra positional must be rejected
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["import", "polygon", "abc def ghi"])


# ── 7. incident email carries deposit / expira / resgate ──────────────────
def test_incident_email_has_operational_data(isolated_home):
    from nakpix import incidents

    p = {
        "ordem_id": "PX1", "pix_brl": 10.0, "chave": "a@b.com",
        "network": "polygon", "token": "USDT", "usdt": 2.0,
        "tx_hash": "0x1", "ts": int(time.time()) - 1000,
    }
    _, body = incidents.build_incident_email(
        p, {"status": "pending", "deposit": "0xAAA", "expira": 1234567890}
    )
    assert "Endereço de depósito: 0xAAA" in body
    assert "Expira em:" in body
    assert "[Resgate]" in body
