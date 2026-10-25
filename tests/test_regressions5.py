"""Regression tests for the FIFTH review round.

1. Recovery never associates an unrelated payment by PIX key + value + network —
   it uses only the explicit occurrence→order association recorded before funds
   move (two same-key+value payments, manual + scheduled, interruption after
   blockchain send).
2. A confirmed refund is a distinct 'refunded' state, never 'done' / success.
3. Incident notifications are never duplicated on email retry.

No money, no network.
"""

from __future__ import annotations

import importlib
import time


# ── 1. explicit payment association (P0) ───────────────────────────────────
def test_recovery_does_not_associate_unrelated_manual_payment(isolated_home):
    from nakpix import storage

    import nakpix.scheduler as sched

    importlib.reload(sched)
    storage.init()
    now = int(time.time())
    sid = storage.add_schedule(nome="ag", chave="a@b.com", valor_brl=50.0,
                               network="polygon", recurrencia="once",
                               proxima_ocorrencia=now - 10000)
    occ_id = storage.add_occurrence(sid, now - 10000)
    # association recorded BEFORE funds moved
    storage.update_occurrence(occ_id, status="running", executed_ts=now - 10000,
                              ordem_id="SCHED_ORDER")
    # unrelated manual payment: same chave/valor/rede, created even earlier
    storage.record_payment(ordem_id="MANUAL_ORDER", pix_brl=50.0, chave="a@b.com",
                           network="polygon", token="USDT", usdt=10.0,
                           tx_hash="0xMANUAL", status="sent")
    with storage.conn() as c:
        c.execute("UPDATE payments SET ts=? WHERE ordem_id='MANUAL_ORDER'",
                  (now - 20000,))
    # the scheduled payment (explicitly linked), created later
    storage.record_payment(ordem_id="SCHED_ORDER", pix_brl=50.0, chave="a@b.com",
                           network="polygon", token="USDT", usdt=10.0,
                           tx_hash="0xSCHED", status="funding")
    with storage.conn() as c:
        c.execute("UPDATE payments SET ts=? WHERE ordem_id='SCHED_ORDER'",
                  (now - 9950,))

    assert sched.recover_stale_running(now) == 1
    occ = storage.get_occurrence(occ_id)
    assert occ["ordem_id"] == "SCHED_ORDER"      # NOT the manual order
    assert occ["tx_hash"] == "0xSCHED"
    assert occ["status"] == "pending"            # funding → pending (not done)


def test_recovery_transmission_but_result_not_recorded(isolated_home):
    """Order created (ordem_id persisted) + broadcast, but the local payment
    row was never written (crash). Recovery consults the backend by the EXPLICIT
    order id — an unrelated manual payment with the same key+value is ignored."""
    from nakpix import storage

    import nakpix.scheduler as sched

    importlib.reload(sched)
    storage.init()
    now = int(time.time())
    sid = storage.add_schedule(nome="ag", chave="a@b.com", valor_brl=50.0,
                               network="polygon", recurrencia="once",
                               proxima_ocorrencia=now - 10000)
    occ_id = storage.add_occurrence(sid, now - 10000)
    storage.update_occurrence(occ_id, status="running", executed_ts=now - 10000,
                              ordem_id="SCHED_ORDER")
    # unrelated manual payment (same key+value) — must NOT be picked up
    storage.record_payment(ordem_id="MANUAL_ORDER", pix_brl=50.0, chave="a@b.com",
                           network="polygon", token="USDT", usdt=10.0,
                           tx_hash="0xMANUAL", status="sent")

    class Backend:
        def order_status(self, ordem, network=None):
            assert ordem == "SCHED_ORDER"      # resolved by explicit id only
            return {"status": "funding", "resgate": None}

    assert sched.recover_stale_running(now, Backend()) == 1
    occ = storage.get_occurrence(occ_id)
    assert occ["ordem_id"] == "SCHED_ORDER"
    assert occ["status"] == "pending"          # deposit arrived, awaiting PIX
    assert storage.get_schedule(sid)["status"] == "done"


# ── 2. refund is NOT a completed PIX (P1) ─────────────────────────────────
def test_occurrence_status_separates_refund():
    import nakpix.scheduler as sched

    assert sched._occurrence_status("sent") == "done"
    assert sched._occurrence_status("refunded") == "refunded"
    assert sched._occurrence_status("extornado") == "refunded"


def test_reconcile_refunded_not_done(isolated_home):
    from nakpix import storage

    import nakpix.scheduler as sched

    importlib.reload(sched)
    storage.init()
    now = int(time.time())
    sid = storage.add_schedule(nome="ag", chave="a@b.com", valor_brl=50.0,
                               network="polygon", recurrencia="once",
                               proxima_ocorrencia=now - 1000)
    occ_id = storage.add_occurrence(sid, now - 1000)
    storage.update_occurrence(occ_id, status="pending", ordem_id="PX_REF")

    class Backend:
        def order_status(self, ordem, network=None):
            return {"status": "refunded", "resgate": None}

    assert sched.reconcile_pending_occurrences(Backend()) == 1
    occ = storage.get_occurrence(occ_id)
    assert occ["status"] == "refunded"          # NOT 'done'
    resolved = [n for n in storage.pending_notifications()
                if n["payload"].get("kind") == "scheduled_resolved"]
    assert len(resolved) == 1
    assert resolved[0]["payload"]["status"] == "refunded"   # NOT 'sent'/success


# ── 3. no duplicate incident notifications (P1) ───────────────────────────
def test_email_failure_retry_no_duplicate_notification(isolated_home):
    from nakpix import incidents, server, storage

    storage.init()
    now = int(time.time())
    storage.record_payment(ordem_id="PX_DUP", pix_brl=50.0, chave="a@b.com",
                           network="polygon", token="USDT", usdt=10.0,
                           tx_hash="0xDUP", status="funding")
    with storage.conn() as c:
        c.execute("UPDATE payments SET ts=? WHERE ordem_id='PX_DUP'",
                  (now - 3600,))

    class Backend:
        def order_status(self, ordem, network=None):
            return {"status": "processing"}

    assert incidents.scan_once(Backend()) == 1
    with storage.conn() as c:
        inc = dict(c.execute("SELECT * FROM incidents WHERE ordem_id='PX_DUP'").fetchone())

    # agent reports a failed send (retryable)
    r = server.nakpix_report_incident_email(inc["id"], "failed")
    assert r["email_state"] == "pending"

    # re-dispatch produces exactly ONE in-flight notification (no duplicate)
    assert incidents.scan_once(Backend()) == 1
    inc_notifs = [n for n in storage.pending_notifications()
                  if n["payload"].get("kind") == "incident"]
    assert len(inc_notifs) == 1


def test_undelivered_notification_until_confirmed(isolated_home):
    from nakpix import server, storage

    storage.init()
    inc = storage.create_incident(dedup_key="ORD_U", ordem_id="ORD_U")
    storage.enqueue_notification("agent", {
        "kind": "incident", "incident_id": inc["id"],
        "to": "suporte@nakswap.xyz", "subject": "s", "body": "b",
    })
    assert len(storage.pending_notifications()) == 1   # not delivered yet
    server.nakpix_report_incident_email(inc["id"], "sent")
    assert storage.pending_notifications() == []        # confirmed → delivered


# ── 4. restart during reconciliation is idempotent ────────────────────────
def test_reconcile_idempotent_across_restart(isolated_home):
    from nakpix import storage

    import nakpix.scheduler as sched

    importlib.reload(sched)
    storage.init()
    now = int(time.time())
    sid = storage.add_schedule(nome="ag", chave="a@b.com", valor_brl=50.0,
                               network="polygon", recurrencia="once",
                               proxima_ocorrencia=now - 1000)
    occ_id = storage.add_occurrence(sid, now - 1000)
    storage.update_occurrence(occ_id, status="pending", ordem_id="PX_IDEM")

    class Backend:
        def order_status(self, ordem, network=None):
            return {"status": "sent", "resgate": "NAK-IDEM"}

    assert sched.reconcile_pending_occurrences(Backend()) == 1
    # simulate a restart: reconcile runs again, finds no pending occurrence
    assert sched.reconcile_pending_occurrences(Backend()) == 0
    resolved = [n for n in storage.pending_notifications()
                if n["payload"].get("kind") == "scheduled_resolved"]
    assert len(resolved) == 1
