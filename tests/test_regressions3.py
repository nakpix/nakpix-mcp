"""Regression tests for the THIRD review round (pix_brl immutability, pending
occurrence not 'done', stale 'running' recovery). No money, no network."""

from __future__ import annotations

import importlib
import time


# ── 1. pix_brl=0 must not overwrite the real value ────────────────────────
def test_record_payment_zero_does_not_overwrite_pix_brl(isolated_home):
    from nakpix import storage

    storage.record_payment(
        ordem_id="PX_Z", pix_brl=100.0, chave="a@b.com", network="polygon",
        token="USDT", usdt=20.0, tx_hash="0x1", status="funding",
    )
    storage.record_payment(ordem_id="PX_Z", pix_brl=0.0, network="polygon",
                           status="sent", resgate="NAK1")
    p = storage.get_payment("PX_Z")
    assert p is not None
    assert p["pix_brl"] == 100.0          # NOT replaced by 0
    assert p["status"] == "sent"


# ── 2. a 'pending' PIX must not be recorded as a 'done' occurrence ────────
def test_run_due_pending_occurrence_not_done(isolated_home, monkeypatch):
    from nakpix import storage

    import nakpix.scheduler as sched

    importlib.reload(sched)
    storage.init()
    now = int(time.time())
    sid = storage.add_schedule(
        nome="t", chave="a@b.com", valor_brl=10.0, network="polygon",
        recurrencia="once", proxima_ocorrencia=now - 10,
    )
    # funds sent, still processing
    monkeypatch.setattr(
        sched, "execute_schedule",
        lambda s, c, on_order_created=None: {"status": "pending", "ordem": "PX_PEND"},
    )

    class NoopClient:
        pass

    sched.run_due(NoopClient())
    with storage.conn() as c:
        occ = c.execute("SELECT * FROM occurrences WHERE schedule_id=?", (sid,)).fetchone()
    assert occ["status"] == "pending"     # NOT 'done'
    assert occ["ordem_id"] == "PX_PEND"


# ── 3. a stale 'running' occurrence is recovered (not left stuck) ─────────
def test_recover_stale_running(isolated_home):
    from nakpix import storage

    import nakpix.scheduler as sched

    importlib.reload(sched)
    storage.init()
    now = int(time.time())
    sid = storage.add_schedule(
        nome="t", chave="a@b.com", valor_brl=10.0, network="polygon",
        recurrencia="once", proxima_ocorrencia=now - 10,
    )
    occ_id = storage.add_occurrence(sid, now - 10)
    # simulate a crash mid-execution: occurrence stuck 'running'
    storage.update_occurrence(occ_id, status="running", executed_ts=now - 1000)

    recovered = sched.recover_stale_running(now)
    assert recovered == 1
    occ = storage.get_occurrence(occ_id)
    assert occ["status"] == "investigacao"   # no payment evidence -> not concluded
    sched_rec = storage.get_schedule(sid)
    assert sched_rec["status"] == "active"   # schedule NOT advanced (no transfer)


def test_run_due_recovers_stale_running_before_processing(isolated_home, monkeypatch):
    from nakpix import storage

    import nakpix.scheduler as sched

    importlib.reload(sched)
    storage.init()
    now = int(time.time())
    sid = storage.add_schedule(
        nome="t", chave="a@b.com", valor_brl=10.0, network="polygon",
        recurrencia="once", proxima_ocorrencia=now - 10,
    )
    occ_id = storage.add_occurrence(sid, now - 10)
    storage.update_occurrence(occ_id, status="running", executed_ts=now - 1000)

    calls = []
    monkeypatch.setattr(
        sched, "execute_schedule",
        lambda s, c, on_order_created=None: calls.append(s["id"]) or {"status": "sent", "ordem": "PX_X"},
    )

    class NoopClient:
        pass

    # run_due must first reconcile the stale 'running' (no evidence -> investigate)
    # and NOT re-execute / re-broadcast.
    sched.run_due(NoopClient())
    assert calls == []                      # no re-execution / re-broadcast
    occ = storage.get_occurrence(occ_id)
    assert occ["status"] == "investigacao"
