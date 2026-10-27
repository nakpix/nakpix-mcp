"""Caminhos finais de exposição (revisão v9): pay_pix result e _err bruto.

1) pay_pix()/nakpix_pay_pix só devolvem campos públicos — um payload do
   backend com campos internos (custo/comissão/IP) não chega ao agente.
2) _err/_sanitize_msg neutraliza mensagens com hints de infra/provedor,
   preservando motivos legítimos (saldo, chave, expiração).
"""
import sys
import pytest

sys.path.insert(0, "src")

from nakpix import payments as payments_mod
from nakpix import server


# ───────── 1) pay_pix: whitelist na resposta final ─────────
class _FakeChain:
    def __init__(self, usdt=100.0, native=1.0):
        self._usdt, self._native = usdt, native
    def get_usdt_balance(self, addr):
        return self._usdt
    def get_native_balance(self, addr):
        return self._native


class _LeakyBackend:
    """Backend 'malvado do futuro': devolve campos internos no status final."""
    def create_order(self, **kw):
        return {"ordem": "PIXTEST0001", "deposit": "0x1111", "usdt": 5.0,
                "cupom": "TESTE10", "fee_pct": 6.0}
    def order_status(self, ordem, network=None):
        return {"status": "done", "pix": 25.0, "resgate": "NAK-TEST-0001",
                "custo_provedor_brl": "0.50", "wallet_comissao": "0x999",
                "ip": "203.0.113.7", "saldo_caixa": 99999.5}


def test_pay_pix_filters_internal_fields(monkeypatch):
    monkeypatch.setattr(payments_mod, "create_order", lambda c, **kw: _LeakyBackend().create_order(**kw), raising=False)
    monkeypatch.setattr(payments_mod, "wallet", type("W", (), {
        "exists": staticmethod(lambda n: True),
        "address": staticmethod(lambda n: "0x1111111111111111111111111111111111111111"),
    })())
    monkeypatch.setattr(payments_mod, "get_chain", lambda: _FakeChain(), raising=False)
    monkeypatch.setattr(payments_mod, "fund_order_polygon", lambda **kw: "0xdeadbeef", raising=False)
    monkeypatch.setattr(payments_mod, "poll_order", lambda c, o, network=None: _LeakyBackend().order_status(o))
    monkeypatch.setattr(payments_mod.storage, "record_payment", lambda **kw: None)

    r = payments_mod.pay_pix(_LeakyBackend(), pix_value=25.0, chave="test@example.com")
    assert r["status"] == "done"
    assert r["resgate"] == "NAK-TEST-0001"
    assert r["tx_hash"] == "0xdeadbeef"
    assert r["ordem"] == "PIXTEST0001"
    # campos internos NUNCA chegam
    assert "custo_provedor_brl" not in r
    assert "wallet_comissao" not in r
    assert "ip" not in r
    assert "saldo_caixa" not in r


def test_nakpix_pay_pix_filters_internal_fields(monkeypatch, isolated_home):
    monkeypatch.setattr(server, "get_setting", lambda k, d=None: False)  # sem confirm
    monkeypatch.setattr(server.pix_mod, "validate_chave", lambda c: True)
    monkeypatch.setattr(server, "_client", lambda: _LeakyBackend())

    class _FakePayments:
        PaymentError = payments_mod.PaymentError
        @staticmethod
        def pay_pix(client, **kw):
            return {"ordem": "PIXTEST0001", "tx_hash": "0xdeadbeef",
                    "status": "done", "resgate": "NAK-TEST-0001",
                    "custo_provedor_brl": "0.50", "ip": "10.0.0.9"}
    monkeypatch.setattr(server, "payments_mod", _FakePayments)

    r = server.nakpix_pay_pix(25.0, "test@example.com", confirm=True)
    # status vem do payload (done) — o "ok" do wrapper é sobrescrito, comportamento
    # pré-existente; o que importa: campos públicos presentes, internos ausentes
    assert r["status"] == "done"
    assert r["resgate"] == "NAK-TEST-0001"
    assert "custo_provedor_brl" not in r
    assert "ip" not in r


# ───────── 2) _sanitize_msg / _err ─────────
def test_err_neutralizes_infra_hints():
    r = server._err("backend smartpay respondeu 500 no /pix-quote")
    assert r["msg"] == "falha de comunicação com o backend — tente novamente"
    r2 = server._err("HTTPConnectionPool(host='127.0.0.1', port=8078): timeout")
    assert "127.0.0.1" not in r2["msg"]
    r3 = server._err("nginx: upstream timed out while reading relay.py")
    assert "nginx" not in r3["msg"] and "relay.py" not in r3["msg"]


def test_err_preserves_legitimate_reasons():
    legit = [
        "saldo USDT insuficiente (5.000000 USDT) — necessário 6.0 USDT",
        "carteira sem POL para pagar o gás — deposite POL para a taxa de rede",
        "chave PIX inválida",
        "PIX temporarily unavailable for maintenance — try again soon",
        "cupom inválido",
        "rede solana ainda não habilitada para pagamento imediato",
    ]
    for m in legit:
        assert server._sanitize_msg(m) == m, m


def test_err_empty_and_odd_inputs():
    assert server._sanitize_msg("") == ""
    assert server._sanitize_msg(None) == ""
    assert isinstance(server._err("x")["msg"], str)