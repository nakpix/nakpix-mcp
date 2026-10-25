"""Regression tests for the SEVENTH review round.

P0 — exception during blockchain transmission must NOT be recorded as a
definitive failure. An uncertain broadcast (RPC timeout / lost response) keeps
the occurrence in `investigacao`, does NOT advance the schedule, and does NOT
re-send. The signed tx hash is persisted BEFORE the broadcast so reconciliation
can consult the blockchain by hash (mined → recovered; notfound → proven no
transfer) and the backend by order id.

No money, no network (all RPC/blockchain calls are monkeypatched).
"""

from __future__ import annotations

import importlib
import time


def _reload_sched():
    import nakpix.scheduler as sched

    importlib.reload(sched)
    return sched


# ── unit: status mapping ──────────────────────────────────────────────────
def test_occurrence_status_sending_is_investigacao():
    import nakpix.scheduler as sched

    assert sched._occurrence_status("sending") == "investigacao"
    assert sched._occurrence_status("unknown") == "investigacao"
    assert sched._occurrence_status("failed") == "failed"


# ── run_due: uncertain broadcast does not conclude / advance ──────────────
def test_run_due_uncertain_broadcast_keeps_investigacao(isolated_home, monkeypatch):
    from nakpix import storage

    sched = _reload_sched()
    storage.init()
    now = int(time.time())
    sid = storage.add_schedule(nome="t", chave="a@b.com", valor_brl=50.0,
                               network="polygon", recurrencia="once",
                               proxima_ocorrencia=now - 10)

    monkeypatch.setattr(
        sched, "execute_schedule",
        lambda s, c, on_order_created=None: {
            "status": "sending", "ordem": "PX_SEND", "tx_hash": "0xHASH",
            "erro": "RPC timeout", "resgate": None,
        },
    )

    class NoopClient:
        pass

    sched.run_due(NoopClient())
    with storage.conn() as c:
        occ = c.execute("SELECT * FROM occurrences WHERE schedule_id=?", (sid,)).fetchone()
    assert occ["status"] == "investigacao"      # NOT failed
    assert occ["ordem_id"] == "PX_SEND"
    assert occ["tx_hash"] == "0xHASH"           # persisted before broadcast
    s = storage.get_schedule(sid)
    assert s["status"] == "active"              # NOT advanced / NOT done
    assert s["proxima_ocorrencia"] == now - 10


# ── pay_pix: BroadcastUncertain → 'sending' (not failed) ──────────────────
def test_pay_pix_broadcast_uncertain_returns_sending(isolated_home, monkeypatch):
    import nakpix.payments as payments_mod

    importlib.reload(payments_mod)
    BroadcastUncertain = payments_mod.BroadcastUncertain
    monkeypatch.setattr(payments_mod.wallet, "exists", lambda net: True)
    monkeypatch.setattr(payments_mod.wallet, "address", lambda net: "0xWALLET")
    monkeypatch.setattr(
        payments_mod, "create_order",
        lambda client, **kw: {"ordem": "PX_BC", "deposit": "0xD", "usdt": 10.0,
                              "expira": 9999999999, "cupom": None, "fee_pct": 1.0},
    )

    class Chain:
        def get_usdt_balance(self, a):
            return 100.0

        def get_native_balance(self, a):
            return 1.0

    import nakpix.blockchain.polygon as bp

    monkeypatch.setattr(bp, "get_chain", lambda: Chain())

    def _boom(*, deposit, usdt, ordem, pix_brl, chave):
        raise BroadcastUncertain("0xHASH", "timeout")

    monkeypatch.setattr(payments_mod, "fund_order_polygon", _boom)

    r = payments_mod.pay_pix(payments_mod.BackendClient(), pix_value=50.0,
                             chave="a@b.com", network="polygon")
    assert r["status"] == "sending"             # NOT failed
    assert r["tx_hash"] == "0xHASH"


# ── reconcile by blockchain hash ──────────────────────────────────────────
def _seed_investigacao(storage, ordem, tx_hash, now):
    sid = storage.add_schedule(nome="t", chave="a@b.com", valor_brl=50.0,
                               network="polygon", recurrencia="once",
                               proxima_ocorrencia=now - 10000)
    occ_id = storage.add_occurrence(sid, now - 10000)
    storage.update_occurrence(occ_id, status="investigacao", ordem_id=ordem,
                              tx_hash=tx_hash,
                              erro="recuperação: backend indisponível")
    return sid, occ_id


class _InconclusiveBackend:
    def order_status(self, ordem, network=None):
        return {"status": "processing"}


class _DownBackend:
    def order_status(self, ordem, network=None):
        raise RuntimeError("backend down")


def test_reconcile_mined_recovers_uncertain_broadcast(isolated_home, monkeypatch):
    from nakpix import storage

    sched = _reload_sched()
    storage.init()
    now = int(time.time())
    sid, occ_id = _seed_investigacao(storage, "PX_MINE", "0xHASH", now)

    monkeypatch.setattr(sched, "_tx_state", lambda h: "mined")
    assert sched.reconcile_pending_occurrences(_InconclusiveBackend()) == 1
    occ = storage.get_occurrence(occ_id)
    assert occ["status"] == "pending"           # funds on-chain, awaiting PIX
    assert storage.get_schedule(sid)["status"] == "done"   # schedule advanced


def test_reconcile_notfound_concludes_no_transfer(isolated_home, monkeypatch):
    from nakpix import storage

    sched = _reload_sched()
    storage.init()
    now = int(time.time())
    sid, occ_id = _seed_investigacao(storage, "PX_NOCHAIN", "0xHASH", now)

    monkeypatch.setattr(sched, "_tx_state", lambda h: "notfound")
    assert sched.reconcile_pending_occurrences(_InconclusiveBackend()) == 1
    occ = storage.get_occurrence(occ_id)
    assert occ["status"] == "failed"            # proven no transfer
    assert "on-chain" in (occ["erro"] or "")
    assert storage.get_schedule(sid)["status"] == "done"


def test_reconcile_mempool_or_unknown_stays_investigacao(isolated_home, monkeypatch):
    from nakpix import storage

    sched = _reload_sched()
    storage.init()
    now = int(time.time())
    sid, occ_id = _seed_investigacao(storage, "PX_PEND", "0xHASH", now)

    monkeypatch.setattr(sched, "_tx_state", lambda h: "pending")
    assert sched.reconcile_pending_occurrences(_InconclusiveBackend()) == 0
    assert storage.get_occurrence(occ_id)["status"] == "investigacao"
    assert storage.get_schedule(sid)["status"] == "active"

    monkeypatch.setattr(sched, "_tx_state", lambda h: "unknown")
    assert sched.reconcile_pending_occurrences(_InconclusiveBackend()) == 0
    assert storage.get_occurrence(occ_id)["status"] == "investigacao"
    assert storage.get_schedule(sid)["status"] == "active"


def test_reconcile_backend_down_but_blockchain_mined(isolated_home, monkeypatch):
    # Even when the backend is unreachable, a mined tx hash proves funds were
    # sent — reconcile to pending and advance.
    from nakpix import storage

    sched = _reload_sched()
    storage.init()
    now = int(time.time())
    sid, occ_id = _seed_investigacao(storage, "PX_DOWNMINE", "0xHASH", now)

    monkeypatch.setattr(sched, "_tx_state", lambda h: "mined")
    assert sched.reconcile_pending_occurrences(_DownBackend()) == 1
    assert storage.get_occurrence(occ_id)["status"] == "pending"
    assert storage.get_schedule(sid)["status"] == "done"


# ── recover: a 'sending' payment (tx_hash, unknown result) ────────────────
def test_recover_sending_payment_keeps_investigacao(isolated_home):
    from nakpix import storage

    sched = _reload_sched()
    storage.init()
    now = int(time.time())
    sid = storage.add_schedule(nome="t", chave="a@b.com", valor_brl=50.0,
                               network="polygon", recurrencia="once",
                               proxima_ocorrencia=now - 10000)
    occ_id = storage.add_occurrence(sid, now - 10000)
    storage.update_occurrence(occ_id, status="running", executed_ts=now - 10000,
                              ordem_id="PX_SEND")
    # a 'sending' payment: broadcast attempted, result unknown (tx_hash persisted)
    storage.record_payment(ordem_id="PX_SEND", pix_brl=50.0, chave="a@b.com",
                           network="polygon", token="USDT", usdt=10.0,
                           tx_hash="0xHASH", status="sending")

    assert sched.recover_stale_running(now) == 1
    occ = storage.get_occurrence(occ_id)
    assert occ["status"] == "investigacao"      # NOT failed
    assert occ["tx_hash"] == "0xHASH"
    assert storage.get_schedule(sid)["status"] == "active"   # NOT advanced
