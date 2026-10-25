"""Regression tests for the SIXTH review round.

P0 — `recover_stale_running` must NOT conclude a financial result when the
backend is unreachable / inconclusive: keep `investigacao`, do NOT advance and
do NOT mark failed (absence of a local record is not proof of no transfer).
Only a definitive backend answer (funds moved, or PROVEN no transfer) may
advance/close the schedule. `reconcile_pending_occurrences` must re-check
`investigacao` occurrences and settle them once the backend is conclusive.

No money, no network.
"""

from __future__ import annotations

import importlib
import time


def _seed_running_with_ordem(storage, ordem, now):
    """A schedule + a stale 'running' occurrence with an explicit ordem_id
    recorded before funds moved, and NO local payment record."""
    sid = storage.add_schedule(nome="t", chave="a@b.com", valor_brl=50.0,
                               network="polygon", recurrencia="once",
                               proxima_ocorrencia=now - 10000)
    occ_id = storage.add_occurrence(sid, now - 10000)
    storage.update_occurrence(occ_id, status="running", executed_ts=now - 10000,
                              ordem_id=ordem)
    return sid, occ_id


def _reload():
    import nakpix.scheduler as sched

    importlib.reload(sched)
    return sched


# ── backend unavailable / no client → investigacao (P0) ───────────────────
def test_recover_backend_unavailable_keeps_investigacao(isolated_home):
    from nakpix import storage

    sched = _reload()
    storage.init()
    now = int(time.time())
    sid, occ_id = _seed_running_with_ordem(storage, "PX_BACK", now)

    class DownBackend:
        def order_status(self, ordem, network=None):
            raise RuntimeError("backend down")

    assert sched.recover_stale_running(now, DownBackend()) == 1
    occ = storage.get_occurrence(occ_id)
    assert occ["status"] == "investigacao"          # NOT failed
    assert "backend indisponível" in (occ["erro"] or "")
    s = storage.get_schedule(sid)
    assert s["status"] == "active"                  # NOT advanced / NOT done
    assert s["proxima_ocorrencia"] == now - 10000


def test_recover_no_client_keeps_investigacao(isolated_home):
    from nakpix import storage

    sched = _reload()
    storage.init()
    now = int(time.time())
    sid, occ_id = _seed_running_with_ordem(storage, "PX_NOCLIENT", now)

    assert sched.recover_stale_running(now) == 1    # no client at all
    assert storage.get_occurrence(occ_id)["status"] == "investigacao"
    assert storage.get_schedule(sid)["status"] == "active"


def test_recover_inconclusive_status_keeps_investigacao(isolated_home):
    from nakpix import storage

    sched = _reload()
    storage.init()
    now = int(time.time())
    sid, occ_id = _seed_running_with_ordem(storage, "PX_AGUARD", now)

    class Backend:
        def order_status(self, ordem, network=None):
            return {"status": "aguardando"}          # not conclusive

    assert sched.recover_stale_running(now, Backend()) == 1
    assert storage.get_occurrence(occ_id)["status"] == "investigacao"
    assert storage.get_schedule(sid)["status"] == "active"


# ── proven no transfer → failed + advance ─────────────────────────────────
def test_recover_proven_no_funds_expirada(isolated_home):
    from nakpix import storage

    sched = _reload()
    storage.init()
    now = int(time.time())
    sid, occ_id = _seed_running_with_ordem(storage, "PX_EXP", now)

    class Backend:
        def order_status(self, ordem, network=None):
            return {"status": "expirada"}

    assert sched.recover_stale_running(now, Backend()) == 1
    occ = storage.get_occurrence(occ_id)
    assert occ["status"] == "failed"                # proven no transfer
    assert "sem depósito" in (occ["erro"] or "")
    assert storage.get_schedule(sid)["status"] == "done"


def test_recover_notfound_is_inconclusive(isolated_home):
    # 'notfound' means the backend cannot locate the order yet — it is NOT proof
    # that no funds were sent (the order may be transiently missing). Keep
    # 'investigacao', do NOT advance, do NOT mark failed.
    from nakpix import storage

    sched = _reload()
    storage.init()
    now = int(time.time())
    sid, occ_id = _seed_running_with_ordem(storage, "PX_NF", now)

    class Backend:
        def order_status(self, ordem, network=None):
            return {"status": "notfound"}

    assert sched.recover_stale_running(now, Backend()) == 1
    assert storage.get_occurrence(occ_id)["status"] == "investigacao"
    assert storage.get_schedule(sid)["status"] == "active"


# ── transmission accepted but hash not recorded → reconcile + advance ─────
def test_recover_transmission_accepted_hash_not_recorded(isolated_home):
    from nakpix import storage

    sched = _reload()
    storage.init()
    now = int(time.time())
    sid, occ_id = _seed_running_with_ordem(storage, "PX_FUND", now)

    class Backend:
        def order_status(self, ordem, network=None):
            return {"status": "funding", "resgate": None}   # deposit arrived

    assert sched.recover_stale_running(now, Backend()) == 1
    occ = storage.get_occurrence(occ_id)
    assert occ["status"] == "pending"               # funds moved, awaiting PIX
    assert storage.get_schedule(sid)["status"] == "done"


def test_recover_backend_resolved_sent(isolated_home):
    from nakpix import storage

    sched = _reload()
    storage.init()
    now = int(time.time())
    sid, occ_id = _seed_running_with_ordem(storage, "PX_SENT", now)

    class Backend:
        def order_status(self, ordem, network=None):
            return {"status": "sent", "resgate": "NAK-RES"}

    assert sched.recover_stale_running(now, Backend()) == 1
    occ = storage.get_occurrence(occ_id)
    assert occ["status"] == "done"
    assert occ["resgate"] == "NAK-RES"
    assert storage.get_schedule(sid)["status"] == "done"


# ── investigacao re-checked when the backend returns ──────────────────────
def test_investigacao_reconciled_when_backend_returns(isolated_home):
    from nakpix import storage

    sched = _reload()
    storage.init()
    now = int(time.time())
    sid = storage.add_schedule(nome="t", chave="a@b.com", valor_brl=50.0,
                               network="polygon", recurrencia="once",
                               proxima_ocorrencia=now - 10000)
    occ_id = storage.add_occurrence(sid, now - 10000)
    # recovery previously left this in 'investigacao' (backend was down) and
    # did NOT advance the schedule.
    storage.update_occurrence(occ_id, status="investigacao", ordem_id="PX_RECON",
                              erro="recuperação: backend indisponível")

    class Backend:
        def order_status(self, ordem, network=None):
            return {"status": "sent", "resgate": "NAK-RECON"}

    assert sched.reconcile_pending_occurrences(Backend()) == 1
    occ = storage.get_occurrence(occ_id)
    assert occ["status"] == "done"
    assert occ["resgate"] == "NAK-RECON"
    # the schedule is now advanced/closed once the result was conclusive
    assert storage.get_schedule(sid)["status"] == "done"
    resolved = [n for n in storage.pending_notifications()
                if n["payload"].get("kind") == "scheduled_resolved"]
    assert len(resolved) == 1


def test_investigacao_not_reconciled_while_backend_down(isolated_home):
    from nakpix import storage

    sched = _reload()
    storage.init()
    now = int(time.time())
    sid = storage.add_schedule(nome="t", chave="a@b.com", valor_brl=50.0,
                               network="polygon", recurrencia="once",
                               proxima_ocorrencia=now - 10000)
    occ_id = storage.add_occurrence(sid, now - 10000)
    storage.update_occurrence(occ_id, status="investigacao", ordem_id="PX_DOWN")

    class DownBackend:
        def order_status(self, ordem, network=None):
            raise RuntimeError("backend down")

    assert sched.reconcile_pending_occurrences(DownBackend()) == 0
    assert storage.get_occurrence(occ_id)["status"] == "investigacao"
    assert storage.get_schedule(sid)["status"] == "active"
