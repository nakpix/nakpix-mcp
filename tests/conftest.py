"""Shared fixtures: isolated NAKPIX_HOME for every test, plus a fake backend."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture()
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("NAKPIX_HOME", str(tmp_path / ".nakpix"))
    monkeypatch.setenv("NAKPIX_BACKEND_URL", "http://backend.test")
    # reset module-level state (blockchain chain singleton)
    from nakpix import blockchain

    blockchain.polygon._chain = None
    # re-import config to pick up new NAKPIX_HOME (path is computed at import).
    # Reload the whole client chain so every module re-binds the isolated
    # config/storage (server.get_setting, payments.storage, etc.) — otherwise
    # a module imported earlier keeps pointing at the real ~/.nakpix.
    import importlib

    for mod in ("nakpix.config", "nakpix.storage", "nakpix.wallet_agent.manager",
                "nakpix.contacts", "nakpix.coupons", "nakpix.api", "nakpix.pix",
                "nakpix.quotes", "nakpix.payments",
                "nakpix.scheduler", "nakpix.incidents", "nakpix.server"):
        if mod in sys.modules:
            importlib.reload(sys.modules[mod])
    yield tmp_path


class FakeBackend:
    """Deterministic backend double for tests (no network)."""

    def __init__(self):
        self.orders = {}

    def quote(self, brl, network=None):
        # 5 BRL per USDT, with the fields the relay uses
        return {"status": "ok", "quote": {
            "total_brl": brl, "total_pxusdt": brl / 5.0, "data": {}}
        }

    def pix_quote(self, brl, cupom="", network=None):
        # backend /pix-quote -> campos de cliente (o MCP NAO calcula custo)
        fee = 6.0 if cupom == "TESTE10" else 9.0
        desconto = 3.0 if cupom == "TESTE10" else 0.0
        return {"pix_brl": float(brl), "usdt": round(float(brl) / 5.0, 6),
                "fee_pct": fee, "fee_base": 9.0, "cambio": 5.0,
                "cupom": cupom, "desconto": desconto}

    def validate_coupon(self, codigo, network=None):
        if codigo == "TESTE10":
            return {"ok": True, "codigo": codigo, "pct": 6.0, "wallet": "0x..", "usos": 0}
        return {"ok": False, "msg": "invalid coupon", "pct": 5.0}

    def create_order(self, **kw):
        oid = "PIXTEST0001"
        self.orders[oid] = kw
        return {"ordem": oid, "deposit": "0x1111111111111111111111111111111111111111",
                "usdt": 20.0, "expira": 9999999999}

    def order_status(self, ordem, network=None):
        return {"status": "sent", "resgate": "NAK-TEST-0001"}

    def receipt_url(self, **kw):
        return "http://backend.test/comprovante?" + "&".join(f"{k}={v}" for k, v in kw.items())


@pytest.fixture()
def fake_backend():
    return FakeBackend()
