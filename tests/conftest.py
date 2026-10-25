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
    # re-import config to pick up new NAKPIX_HOME (path is computed at import)
    import importlib

    for mod in ("nakpix.config", "nakpix.storage", "nakpix.wallet_agent.manager",
                "nakpix.contacts", "nakpix.coupons"):
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

    def validate_coupon(self, codigo, network=None):
        if codigo == "PARCEIRO10":
            return {"ok": True, "codigo": codigo, "pct": 1.5, "wallet": "0x..", "usos": 0}
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
