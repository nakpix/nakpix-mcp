"""Whitelist de campos do backend (revisão — ponto 3).

Se o backend um dia devolver um campo interno (custo, carteira de comissao,
IP, etc.), ele NAO entra no contexto do agente: _filter_backend deixa passar
apenas os campos públicos de cliente.
"""
import sys
sys.path.insert(0, "src")

from nakpix import server


def test_filter_keeps_public_fields():
    st = {"status": "done", "pix": 320.0, "deposit": "0xABC", "usdt": 62.29,
           "resgate": "NAK-TEST-0001", "ordem": "PIXTEST0001", "expira": 123,
           "erro": None, "txid": "E123", "quando": 456}
    out = server._filter_backend(st)
    assert out == st   # tudo publico passa


def test_filter_drops_internal_fields():
    st = {"status": "done", "pix": 320.0, "resgate": "NAK-TEST-0001",
           "custo_provedor_brl": "3.40", "bruto_brl": "323.40",
           "margem_brl": "0.99", "wallet_comissao": "0x999",
           "ip": "203.0.113.7", "saldo_caixa": 15234.5, "owner": "0x888"}
    out = server._filter_backend(st)
    assert out == {"status": "done", "pix": 320.0, "resgate": "NAK-TEST-0001"}
    assert "custo_provedor_brl" not in out
    assert "wallet_comissao" not in out
    assert "ip" not in out


def test_filter_non_dict_passthrough():
    assert server._filter_backend("x") == "x"
    assert server._filter_backend(None) is None


def test_get_payment_status_filters(monkeypatch):
    class FakeClient:
        def order_status(self, ordem, network=None):
            # backend malvado do futuro: devolve TUDO
            return {"status": "done", "pix": 25.0, "resgate": "NAK-TEST-0001",
                    "custo_provedor_brl": "0.50", "comissao_wallet": "0x123"}
    monkeypatch.setattr(server, "_client", lambda: FakeClient())
    r = server.nakpix_get_payment_status("PIXTEST0001")
    assert r["status"] == "done"
    assert "custo_provedor_brl" not in r
    assert "comissao_wallet" not in r
    assert r["resgate"] == "NAK-TEST-0001"


def test_get_transaction_details_filters(monkeypatch):
    class FakeClient:
        def order_status(self, ordem, network=None):
            return {"status": "sent", "pix": 10.0, "ip": "10.0.0.9",
                    "saldo_interno": 999}
    monkeypatch.setattr(server, "_client", lambda: FakeClient())
    monkeypatch.setattr(server.storage, "get_payment", lambda o: {"local": True})
    r = server.nakpix_get_transaction_details("PIXTEST0001")
    assert r["backend"]["status"] == "sent"
    assert "ip" not in r["backend"]
    assert "saldo_interno" not in r["backend"]