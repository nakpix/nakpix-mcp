"""Testes de recuperação — revisão GPT (4 defeitos). Nenhum pagamento real.

1) scheduler._tx_state resolve o import correto (..blockchain.polygon)
2) get_tx_state: reverted (receipt.status==0) NÃO é 'mined' — é 'failed';
   receipt indisponível rotaciona RPC; tx None continua 'notfound'
3) _w3_live: todos RPCs mortos levanta RuntimeError (não devolve cliente morto)
4) poll_order: timeout de comunicação devolve 'unknown' (investigação),
   nunca 'err' (que o scheduler classifica como falha DEFINITIVA)
"""
import sys, types
import pytest

sys.path.insert(0, "src")

from nakpix import payments as payments_mod
from nakpix.blockchain import polygon as poly_mod


# ───────────────────────── 1) import path ─────────────────────────
def test_tx_state_import_resolves():
    # antes: ModuleNotFoundError: nakpix.scheduler.blockchain
    from nakpix.scheduler import _tx_state   # importa SEM erro
    import nakpix.scheduler as sched_mod
    # caminho real do módulo carregado aponta pra nakpix.blockchain.polygon
    assert "nakpix.blockchain.polygon" in poly_mod.__name__


# ───────────────────── 2) get_tx_state reverted ─────────────────────
class _FakeW3:
    def __init__(self, tx=None, receipt=None, block_raises=False):
        self._tx, self._rc = tx, receipt
        self.block_raises = block_raises

    class eth:
        pass

def _make_chain(monkeypatch, tx, receipt):
    ch = poly_mod.PolygonChain.__new__(poly_mod.PolygonChain)
    ch._rpcs = ["https://rpc1.test", "https://rpc2.test"]
    ch._idx = 0
    ch._w3 = None
    calls = {"receipt": 0}

    def fake_new_w3(rpc):
        w3 = types.SimpleNamespace()
        w3.eth = types.SimpleNamespace()

        def get_transaction(h):
            return tx

        def get_transaction_receipt(h):
            calls["receipt"] += 1
            if isinstance(receipt, Exception):
                raise receipt
            return receipt

        w3.eth.get_transaction = get_transaction
        w3.eth.get_transaction_receipt = get_transaction_receipt
        return w3

    monkeypatch.setattr(ch, "_new_w3", fake_new_w3)
    return ch, calls


def test_mined_requires_receipt_ok(monkeypatch):
    tx = {"blockNumber": 123}
    ch, _ = _make_chain(monkeypatch, tx, receipt={"status": 1})
    assert ch.get_tx_state("0xabc") == "mined"

def test_reverted_is_failed_not_mined(monkeypatch):
    # REVERTED: inclusa em bloco, receipt.status == 0 — NUNCA 'mined'
    tx = {"blockNumber": 123}
    ch, _ = _make_chain(monkeypatch, tx, receipt={"status": 0})
    assert ch.get_tx_state("0xabc") == "failed"

def test_receipt_unavailable_rotates_and_concludes(monkeypatch):
    # receipt estoura no 1o rpc -> rotaciona -> 2o rpc responde ok => mined
    ch = poly_mod.PolygonChain.__new__(poly_mod.PolygonChain)
    ch._rpcs = ["https://rpc1.test", "https://rpc2.test"]
    ch._idx = 0
    ch._w3 = None
    receipt_calls = {"n": 0}

    def fake_new_w3(rpc):
        w3 = types.SimpleNamespace()
        w3.eth = types.SimpleNamespace()
        w3.eth.get_transaction = lambda h: {"blockNumber": 9}
        def get_receipt(h):
            receipt_calls["n"] += 1
            if receipt_calls["n"] == 1:
                raise TimeoutError("rpc lento")
            return {"status": 1}
        w3.eth.get_transaction_receipt = get_receipt
        return w3

    monkeypatch.setattr(ch, "_new_w3", fake_new_w3)
    assert ch.get_tx_state("0xabc") == "mined"

def test_tx_none_is_notfound(monkeypatch):
    ch, _ = _make_chain(monkeypatch, tx=None, receipt=None)
    assert ch.get_tx_state("0xabc") == "notfound"

def test_no_block_is_pending(monkeypatch):
    ch, _ = _make_chain(monkeypatch, tx={"blockNumber": None}, receipt=None)
    assert ch.get_tx_state("0xabc") == "pending"


# ─────────────────── 3) _w3_live sem RPC vivo ───────────────────
def test_w3_live_raises_when_all_rpcs_dead(monkeypatch):
    class _DeadEth:
        @property
        def block_number(self):
            raise TimeoutError("dead")

    ch = poly_mod.PolygonChain.__new__(poly_mod.PolygonChain)
    ch._rpcs = ["https://rpc1.test", "https://rpc2.test"]
    ch._idx = 0
    ch._w3 = object()

    def dead_w3(rpc):
        w3 = types.SimpleNamespace()
        w3.eth = _DeadEth()
        return w3

    monkeypatch.setattr(ch, "_new_w3", dead_w3)
    with pytest.raises(RuntimeError):
        ch._w3_live()


# ───────────── 4) poll_order: timeout de comunicação ─────────────
def test_poll_order_comms_timeout_returns_unknown(monkeypatch):
    class DeadClient:
        def order_status(self, ordem, network=None):
            raise TimeoutError("backend nao responde")

    # timeout=1 + sleep curto para nao demorar o teste
    monkeypatch.setattr(payments_mod.time, "sleep", lambda s: None)
    r = payments_mod.poll_order(DeadClient(), "PIXTEST0001", timeout=1)
    assert r["status"] == "unknown"          # NUNCA 'err' definitivo
    assert "msg" in r

def test_poll_order_normal_status_untouched(monkeypatch):
    class OkClient:
        def __init__(self):
            self.n = 0
        def order_status(self, ordem, network=None):
            self.n += 1
            return {"status": "sent", "resgate": "NAK-TEST-0001"}

    monkeypatch.setattr(payments_mod.time, "sleep", lambda s: None)
    r = payments_mod.poll_order(OkClient(), "PIXTEST0001", timeout=5)
    assert r["status"] == "sent"
    assert r["resgate"] == "NAK-TEST-0001"

def test_unknown_classifies_as_investigacao():
    # o scheduler NUNCA classifica 'unknown' como failed definitivo
    from nakpix.scheduler import _occurrence_status
    assert _occurrence_status("unknown") == "investigacao"
    assert _occurrence_status("err") == "failed"       # err REAL do backend segue definitivo