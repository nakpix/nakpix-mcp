"""Regression tests for the GPT code-review fixes (no money, no network).

Covers:
  1. scheduler idempotency (unique occurrence per schedule+due time)
  2. record_payment upsert (no financial data loss)
  3. import_wallet no longer accepts a secret over MCP
  4. get_receipt only returns a URL for a real resgate code
  5. incidents: no 500-cap miss + email carries timestamps/refund status
"""

from __future__ import annotations

import importlib
import time

import pytest


# ── 1. Scheduler idempotency ──────────────────────────────────────────────
def test_add_occurrence_idempotent(isolated_home):
    from nakpix import storage

    storage.init()
    occ1 = storage.add_occurrence(1, 100)
    occ2 = storage.add_occurrence(1, 100)  # same (schedule_id, due_ts)
    occ3 = storage.add_occurrence(1, 200)  # different due_ts is allowed
    assert occ1 is not None
    assert occ2 is None
    assert occ3 is not None


def test_occurrences_unique_index_blocks_duplicate(isolated_home):
    import sqlite3

    from nakpix import storage

    storage.init()
    with storage.conn() as c:
        c.execute(
            "INSERT INTO occurrences (schedule_id, due_ts, status) VALUES (7, 42, 'pending')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            c.execute(
                "INSERT INTO occurrences (schedule_id, due_ts, status) VALUES (7, 42, 'pending')"
            )


def test_run_due_skips_existing_occurrence(isolated_home, monkeypatch):
    from nakpix import storage

    import nakpix.scheduler as sched

    importlib.reload(sched)  # bind the freshly-reloaded storage (isolated DB)
    storage.init()
    now = int(time.time())
    sid = storage.add_schedule(
        nome="t", chave="a@b.com", valor_brl=10.0, network="polygon",
        recurrencia="once", proxima_ocorrencia=now - 10,
    )
    # a stale occurrence already exists for this due time (status pending —
    # NOT done), which the old check would have ignored and double-executed.
    storage.add_occurrence(sid, now - 10)

    calls = []
    monkeypatch.setattr(
        sched, "execute_schedule",
        lambda s, c: calls.append(s["id"]) or {"status": "sent", "ordem": "PX_FAKE"},
    )

    class NoopClient:
        pass

    assert sched.run_due(NoopClient()) == 0
    assert calls == []


# ── 2. record_payment upsert (no data loss) ───────────────────────────────
def test_record_payment_upsert_preserves_fields(isolated_home):
    from nakpix import storage

    storage.record_payment(
        ordem_id="PX1", pix_brl=50.0, chave="a@b.com", network="polygon",
        token="USDT", usdt=10.0, status="funding", tx_hash="0xdead",
    )
    # partial update (no chave/token) must NOT wipe already-stored values
    storage.record_payment(
        ordem_id="PX1", pix_brl=50.0, network="polygon",
        status="sent", resgate="NAK1", cupom="TESTE10", fee_pct=1.0,
    )
    p = storage.get_payment("PX1")
    assert p is not None
    assert p["chave"] == "a@b.com"
    assert p["pix_brl"] == 50.0
    assert p["token"] == "USDT"
    assert p["tx_hash"] == "0xdead"     # preserved (not overwritten to NULL)
    assert p["status"] == "sent"        # status always advances
    assert p["resgate"] == "NAK1"
    assert p["cupom"] == "TESTE10"


# ── 3. import_wallet must not take a secret over MCP ──────────────────────
def test_import_wallet_blocks_mcp_secret(isolated_home):
    from nakpix import server

    r = server.nakpix_import_wallet("polygon")
    assert r["status"] == "blocked"
    assert "nakpix-wallet-agent import" in r["msg"]


# ── 4. receipt only for a real resgate code ───────────────────────────────
def test_get_receipt_pendente_sem_resgate(isolated_home, monkeypatch):
    from nakpix import server

    class NoResgate:
        def order_status(self, ordem, network=None):
            return {"status": "processing"}

    monkeypatch.setattr(server, "_client", lambda: NoResgate())
    r = server.nakpix_get_receipt("PIXTEST")
    assert r["status"] == "pendente"
    assert "receipt_url" not in r


def test_get_receipt_ok_com_resgate(isolated_home, monkeypatch):
    from nakpix import server

    class ComResgate:
        def order_status(self, ordem, network=None):
            return {"status": "sent", "resgate": "NAK-ABC", "pix": "10.00"}

        def receipt_url(self, **kw):
            return "http://x/comprovante?code=" + kw["code"]

    monkeypatch.setattr(server, "_client", lambda: ComResgate())
    r = server.nakpix_get_receipt("PIXTEST")
    assert r["status"] == "sent"
    assert r["resgate"] == "NAK-ABC"
    assert r["receipt_url"] == "http://x/comprovante?code=NAK-ABC"


# ── 5. incidents: full scan + email completeness ──────────────────────────
def test_list_unresolved_payments_filters(isolated_home):
    from nakpix import storage

    storage.init()
    now = int(time.time())
    with storage.conn() as c:
        c.execute(
            "INSERT INTO payments (ts, ordem_id, pix_brl, network, tx_hash, status) "
            "VALUES (?,?,?,?,?,?)",
            (now - 3600, "OLD_UNRESOLVED", 10.0, "polygon", "0x1", "pending"),
        )
        c.execute(
            "INSERT INTO payments (ts, ordem_id, pix_brl, network, tx_hash, status) "
            "VALUES (?,?,?,?,?,?)",
            (now - 100, "RECENT_UNRESOLVED", 10.0, "polygon", "0x2", "pending"),
        )
        c.execute(
            "INSERT INTO payments (ts, ordem_id, pix_brl, network, tx_hash, status) "
            "VALUES (?,?,?,?,?,?)",
            (now - 3600, "DONE", 10.0, "polygon", "0x3", "done"),
        )
        c.execute(
            "INSERT INTO payments (ts, ordem_id, pix_brl, network, status) "
            "VALUES (?,?,?,?,?)",
            (now - 3600, "NO_TX", 10.0, "polygon", "pending"),
        )

    cutoff = now - 1000
    ids = [r["ordem_id"] for r in storage.list_unresolved_payments(cutoff)]
    assert "OLD_UNRESOLVED" in ids
    assert "RECENT_UNRESOLVED" not in ids   # too recent
    assert "DONE" not in ids                 # terminal status
    assert "NO_TX" not in ids                # no on-chain tx


def test_incident_email_has_timestamps_and_refund(isolated_home):
    from nakpix import incidents

    p = {
        "ordem_id": "PX1", "pix_brl": 10.0, "chave": "a@b.com",
        "network": "polygon", "token": "USDT", "usdt": 2.0,
        "tx_hash": "0x1", "ts": int(time.time()) - 1000,
    }
    subject, body = incidents.build_incident_email(p, {"status": "extornando"})
    assert "[Horários]" in body
    assert "Criado em:" in body
    assert "[Situação do estorno]" in body
    assert "Extorno em andamento" in body

    # non-refunding status reports no confirmed refund
    _, body2 = incidents.build_incident_email(p, {"status": "pending"})
    assert "Nenhum estorno confirmado" in body2
