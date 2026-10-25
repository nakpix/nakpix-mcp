"""Regression tests for the FOURTH review round (incident email state machine,
real schedule recovery, pending-occurrence conciliation, notification ack,
CLI import message). No money, no network."""

from __future__ import annotations

import importlib
import time


# ── incident email state machine ──────────────────────────────────────────
def _seed_unresolved_payment(storage, ordem="PX_INC", hours=1):
    now = int(time.time())
    storage.record_payment(ordem_id=ordem, pix_brl=50.0, chave="a@b.com",
                           network="polygon", token="USDT", usdt=10.0,
                           tx_hash="0xABC", status="funding")
    with storage.conn() as c:
        c.execute(f"UPDATE payments SET ts=? WHERE ordem_id=?", (now - hours * 3600, ordem))
    return now


class _FakeBackend:
    def __init__(self, status="processing", resgate=None):
        self.status = status
        self.resgate = resgate

    def order_status(self, ordem, network=None):
        return {"status": self.status, "resgate": self.resgate}


def test_incident_notification_state_machine(isolated_home):
    from nakpix import storage, incidents

    _seed_unresolved_payment(storage)
    created = incidents.scan_once(_FakeBackend())
    assert created == 1
    with storage.conn() as c:
        inc = dict(c.execute("SELECT * FROM incidents WHERE ordem_id='PX_INC'").fetchone())
    assert inc["email_state"] == "pending"   # NOT 'sent' before the agent acts
    assert inc["email_sent"] == 0            # legacy flag still 0

    notifs = storage.pending_notifications()
    inc_notifs = [n for n in notifs if n["payload"].get("kind") == "incident"]
    assert len(inc_notifs) == 1
    pl = inc_notifs[0]["payload"]
    assert pl["to"] == "suporte@nakswap.xyz"   # explicit destination
    assert pl["incident_id"] == inc["id"]
    assert pl["subject"] and pl["body"]


def test_scan_once_dispatches_only_once_offline(isolated_home):
    from nakpix import storage, incidents

    _seed_unresolved_payment(storage)
    assert incidents.scan_once(_FakeBackend()) == 1
    # agent still offline: a second scan must NOT re-dispatch
    assert incidents.scan_once(_FakeBackend()) == 0
    inc_notifs = [n for n in storage.pending_notifications()
                  if n["payload"].get("kind") == "incident"]
    assert len(inc_notifs) == 1


def test_report_incident_email_state_machine(isolated_home):
    from nakpix import server, storage

    storage.init()
    inc = storage.create_incident(dedup_key="ORD_Y", ordem_id="ORD_Y")
    # 2 failures below the cap keep it retryable (pending)
    for _ in range(2):
        r = server.nakpix_report_incident_email(inc["id"], "failed")
        assert r["email_state"] == "pending"
    # 3rd failure gives up
    r = server.nakpix_report_incident_email(inc["id"], "failed")
    assert r["email_state"] == "failed"
    assert storage.get_incident(inc["id"])["email_attempts"] == 3


def test_report_incident_email_sent_marks_delivered(isolated_home):
    from nakpix import server, storage

    storage.init()
    inc = storage.create_incident(dedup_key="ORD_S", ordem_id="ORD_S")
    storage.enqueue_notification("agent", {
        "kind": "incident", "incident_id": inc["id"],
        "to": "suporte@nakswap.xyz", "subject": "s", "body": "b",
    })
    r = server.nakpix_report_incident_email(inc["id"], "sent")
    assert r["email_state"] == "sent"
    assert storage.get_incident(inc["id"])["email_state"] == "sent"
    assert storage.pending_notifications() == []   # notification delivered


def test_report_incident_email_instructed(isolated_home):
    from nakpix import server, storage

    storage.init()
    inc = storage.create_incident(dedup_key="ORD_I", ordem_id="ORD_I")
    r = server.nakpix_report_incident_email(inc["id"], "instructed")
    assert r["email_state"] == "instructed"


# ── real schedule recovery ────────────────────────────────────────────────
def test_recover_stale_running_with_payment(isolated_home):
    from nakpix import storage

    import nakpix.scheduler as sched

    importlib.reload(sched)
    storage.init()
    now = int(time.time())
    sid = storage.add_schedule(nome="t", chave="a@b.com", valor_brl=10.0, network="polygon",
                               recurrencia="once", proxima_ocorrencia=now - 10000)
    occ_id = storage.add_occurrence(sid, now - 10000)
    storage.update_occurrence(occ_id, status="running", executed_ts=now - 10000)
    # transfer WAS sent: a payment record exists (recover ordem + hash)
    storage.record_payment(ordem_id="PX_REC", pix_brl=10.0, chave="a@b.com",
                           network="polygon", token="USDT", usdt=2.0,
                           tx_hash="0xREC", status="funding")
    with storage.conn() as c:
        c.execute("UPDATE payments SET ts=? WHERE ordem_id='PX_REC'", (now - 9950,))

    assert sched.recover_stale_running(now) == 1
    occ = storage.get_occurrence(occ_id)
    assert occ["status"] == "pending"       # funds sent, not confirmed
    assert occ["ordem_id"] == "PX_REC"      # ordem recovered
    assert storage.get_schedule(sid)["status"] == "done"   # schedule advanced


def test_recover_stale_running_without_payment(isolated_home):
    from nakpix import storage

    import nakpix.scheduler as sched

    importlib.reload(sched)
    storage.init()
    now = int(time.time())
    sid = storage.add_schedule(nome="t", chave="a@b.com", valor_brl=10.0, network="polygon",
                               recurrencia="once", proxima_ocorrencia=now - 10000)
    occ_id = storage.add_occurrence(sid, now - 10000)
    storage.update_occurrence(occ_id, status="running", executed_ts=now - 10000)

    assert sched.recover_stale_running(now) == 1
    occ = storage.get_occurrence(occ_id)
    assert occ["status"] == "investigacao"          # no evidence — not concluded
    sched_rec = storage.get_schedule(sid)
    assert sched_rec["status"] == "active"          # schedule NOT advanced
    assert sched_rec["proxima_ocorrencia"] == now - 10000


def test_recover_stale_running_respects_deadline(isolated_home):
    from nakpix import storage

    import nakpix.scheduler as sched

    importlib.reload(sched)
    storage.init()
    now = int(time.time())
    sid = storage.add_schedule(nome="t", chave="a@b.com", valor_brl=10.0, network="polygon",
                               recurrencia="once", proxima_ocorrencia=now - 100)
    occ_id = storage.add_occurrence(sid, now - 100)
    storage.update_occurrence(occ_id, status="running", executed_ts=now - 10)  # recent

    assert sched.recover_stale_running(now) == 0     # worker may still be alive
    assert storage.get_occurrence(occ_id)["status"] == "running"


# ── pending occurrence conciliation ───────────────────────────────────────
def test_reconcile_pending_occurrence_done(isolated_home):
    from nakpix import storage

    import nakpix.scheduler as sched

    importlib.reload(sched)
    storage.init()
    now = int(time.time())
    sid = storage.add_schedule(nome="t", chave="a@b.com", valor_brl=10.0, network="polygon",
                               recurrencia="once", proxima_ocorrencia=now - 1000)
    occ_id = storage.add_occurrence(sid, now - 1000)
    storage.update_occurrence(occ_id, status="pending", ordem_id="PX_PEND")

    assert sched.reconcile_pending_occurrences(_FakeBackend(status="sent", resgate="NAK-RES")) == 1
    occ = storage.get_occurrence(occ_id)
    assert occ["status"] == "done"
    assert occ["resgate"] == "NAK-RES"
    resolved = [n for n in storage.pending_notifications()
                if n["payload"].get("kind") == "scheduled_resolved"]
    assert len(resolved) == 1


def test_reconcile_pending_occurrence_failed(isolated_home):
    from nakpix import storage

    import nakpix.scheduler as sched

    importlib.reload(sched)
    storage.init()
    now = int(time.time())
    sid = storage.add_schedule(nome="t", chave="a@b.com", valor_brl=10.0, network="polygon",
                               recurrencia="once", proxima_ocorrencia=now - 1000)
    occ_id = storage.add_occurrence(sid, now - 1000)
    storage.update_occurrence(occ_id, status="pending", ordem_id="PX_PEND")

    assert sched.reconcile_pending_occurrences(_FakeBackend(status="erro")) == 1
    assert storage.get_occurrence(occ_id)["status"] == "failed"


# ── notification ack + import message ─────────────────────────────────────
def test_ack_notification(isolated_home):
    from nakpix import server, storage

    storage.init()
    nid = storage.enqueue_notification("agent", {"kind": "reminder", "x": 1})
    r = server.nakpix_ack_notification(nid)
    assert r["status"] == "ok"
    assert storage.pending_notifications() == []


def test_import_wallet_message_no_secret(isolated_home):
    from nakpix import server

    r = server.nakpix_import_wallet("polygon")
    assert "mnemonic-ou-chave-privada" not in r["msg"]
    assert "nakpix-wallet-agent import polygon" in r["msg"]
