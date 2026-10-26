"""Contacts, coupons, quotes, storage, scheduler tests."""

from __future__ import annotations

import time

import pytest

from nakpix import contacts, coupons, quotes, storage
from nakpix.scheduler import next_occurrence


def test_contacts_crud(isolated_home):
    contacts.add_contact("Pai", "+5511999999999")
    assert contacts.get_contact("pai")["chave"] == "+5511999999999"
    assert contacts.find_contact("pa")  # partial match
    contacts.update_contact("pai", chave="pai@email.com")
    assert contacts.get_contact("Pai")["chave"] == "pai@email.com"
    assert contacts.delete_contact("pai")
    assert contacts.get_contact("pai") is None


def test_default_coupon(isolated_home):
    assert coupons.get_default_coupon() is None
    coupons.set_default_coupon("PARCEIRO10")
    assert coupons.get_default_coupon() == "PARCEIRO10"
    coupons.remove_default_coupon()
    assert coupons.get_default_coupon() is None


def test_effective_coupon(isolated_home):
    assert coupons.effective_coupon(None) == ""           # sem default
    coupons.set_default_coupon("PARCEIRO10")
    assert coupons.effective_coupon(None) == "PARCEIRO10"  # default aplicado
    assert coupons.effective_coupon("OUTRO") == "OUTRO"    # explicito vence
    assert coupons.effective_coupon("  spaced  ") == "spaced"  # strip


def test_validate_coupon_does_not_leak_wallet(isolated_home, fake_backend):
    # The backend returns the coupon's commission wallet (internal affiliate
    # destination) and the usage count `usos`. NEITHER may reach the agent/user.
    info = coupons.validate_coupon(fake_backend, "PARCEIRO10")
    assert "wallet" not in info
    assert "usos" not in info
    assert info["codigo"] == "PARCEIRO10"
    assert info["pct"] == 1.5
    # desconto é o que o usuário vê (fee pct fica só interno pro quote)
    assert "desconto" in info


def test_get_quote_uses_backend(isolated_home, fake_backend):
    # o MCP NAO calcula custo — pergunta ao backend /pix-quote
    q = quotes.get_quote(fake_backend, 100.0, "PARCEIRO10")
    assert q["usdt"] > 0
    assert q["fee_pct"] == 1.5
    assert q["cupom"] == "PARCEIRO10"
    assert q["cambio"] == 5.0


def test_storage_payments(isolated_home):
    storage.init()
    storage.record_payment(ordem_id="PIX1", pix_brl=100, network="polygon",
                           usdt=20.0, status="sent", tx_hash="0xabc")
    p = storage.get_payment("PIX1")
    assert p["status"] == "sent"
    assert p["tx_hash"] == "0xabc"


def test_storage_schedule_and_occurrence(isolated_home):
    storage.init()
    sid = storage.add_schedule(nome="Pai", chave="+5511", valor_brl=100,
                               network="polygon", recurrencia="monthly",
                               proxima_ocorrencia=int(time.time()) + 60)
    occ = storage.add_occurrence(sid, int(time.time()) + 60)
    storage.update_occurrence(occ, status="done")
    assert storage.get_occurrence(occ)["status"] == "done"


def test_next_occurrence():
    base = int(time.time())
    s_once = {"recurrencia": "once", "data_fim": None, "max_reps": None, "reps_executadas": 0}
    assert next_occurrence(s_once, base) is None

    s_daily = {"recurrencia": "daily", "data_fim": None, "max_reps": None, "reps_executadas": 0}
    nxt = next_occurrence(s_daily, base)
    assert nxt > base

    s_max = {"recurrencia": "daily", "data_fim": None, "max_reps": 1, "reps_executadas": 0}
    assert next_occurrence(s_max, base) is None  # already at max after 1


def test_incident_dedup(isolated_home):
    storage.init()
    i1 = storage.create_incident(dedup_key="PIX1", ordem_id="PIX1")
    i2 = storage.create_incident(dedup_key="PIX1", ordem_id="PIX1")
    assert i1["id"] == i2["id"]  # deduped


def test_notifications(isolated_home):
    storage.init()
    storage.enqueue_notification("agent", {"kind": "incident", "ordem": "PIX1"})
    pending = storage.pending_notifications("agent")
    assert len(pending) == 1
    storage.mark_notification_delivered(pending[0]["id"])
    assert storage.pending_notifications("agent") == []
