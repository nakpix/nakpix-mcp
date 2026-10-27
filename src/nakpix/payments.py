"""Payment orchestration — immediate PIX via the existing backend.

Flow (Polygon, the live direct path):
  1. estimate cost (quote + coupon fee)
  2. create order on the backend (``/pix-order``) -> deposit address + USDT due
  3. sign + broadcast the USDT transfer from the local wallet to the deposit
  4. poll status until resolved

Money only moves when the agent explicitly calls ``pay`` after the user has
confirmed conversationally. No automatic re-send: idempotency by order id.
"""

from __future__ import annotations

import time

from .api import BackendClient
from . import coupons as coupons_mod
from . import storage
from .wallet_agent import manager as wallet

# USDT ERC-20 transfer selector + USDT contract on Polygon
from .networks import POLYGON_USDT, POLYGON_USDT_DECIMALS, POLYGON_CHAIN_ID


class PaymentError(Exception):
    pass


class BroadcastUncertain(Exception):
    """The broadcast result is UNKNOWN (e.g. RPC timeout, lost response).

    The signed transaction id (``tx_hash``) was already persisted BEFORE the
    broadcast, so the caller can reconcile by blockchain hash — the transfer may
    have been accepted even though the node never confirmed it. This is NOT a
    definitive failure and must never be recorded as ``failed`` or re-sent.
    """

    def __init__(self, tx_hash: str, detail: str):
        super().__init__(detail)
        self.tx_hash = tx_hash
        self.detail = detail


def estimate(client: BackendClient, pix_value: float, cupom: str | None = None, network: str | None = None) -> dict:
    """Cotação vinda do backend (fonte de verdade). Só campos de cliente."""
    return client.pix_quote(pix_value, coupons_mod.effective_coupon(cupom), network)


def create_order(
    client: BackendClient,
    *,
    pix_value: float,
    chave: str,
    cupom: str | None = None,
    extorno: str = "",
    network: str | None = None,
) -> dict:
    r = client.create_order(
        pix_value=pix_value,
        chave=chave,
        cupom=coupons_mod.effective_coupon(cupom),
        fee=None,
        extorno=extorno,
        network=network,
    )
    return {"ordem": r["ordem"], "deposit": r["deposit"], "usdt": r["usdt"],
            "expira": r.get("expira"), "cupom": r.get("cupom") or "", "fee_pct": r.get("fee_pct")}


def _usdt_transfer_data(to_address: str, amount_usdt: float) -> str:
    amount = int(round(amount_usdt * (10 ** POLYGON_USDT_DECIMALS)))
    return (
        "0xa9059cbb"
        + to_address[2:].lower().rjust(64, "0")
        + hex(amount)[2:].rjust(64, "0")
    )


def fund_order_polygon(*, deposit: str, usdt: float, ordem: str, pix_brl: float,
                       chave: str) -> str:
    """Sign + broadcast the USDT transfer to the deposit address. Returns tx hash.

    The tx hash is derivable from the SIGNED bytes, so it is persisted with a
    ``sending`` status BEFORE the broadcast. If the broadcast raises (RPC
    timeout / lost response), ``BroadcastUncertain`` is raised — the transfer is
    NOT recorded as failed and the caller can reconcile by hash later.
    """
    from web3 import Web3

    addr = wallet.address("polygon")
    from .blockchain.polygon import get_chain

    chain = get_chain()
    w3 = chain._w3_live()
    nonce, gas_price = chain.get_tx_params(addr)
    tx = {
        "chainId": POLYGON_CHAIN_ID,
        "nonce": nonce,
        "gas": 80000,
        "maxFeePerGas": int(gas_price * 2),
        "maxPriorityFeePerGas": 30_000_000_000,
        "to": Web3.to_checksum_address(POLYGON_USDT),
        "value": 0,
        "data": _usdt_transfer_data(deposit, usdt),
    }
    raw = wallet.sign_transaction("polygon", tx)
    # Deterministic hash of the signed transaction, known before broadcasting.
    tx_hash = Web3.keccak(raw).hex()
    # Persist the identifier BEFORE the broadcast so an uncertain/interrupted
    # broadcast can always be reconciled by hash (never re-sent).
    storage.record_payment(ordem_id=ordem, pix_brl=pix_brl, chave=chave, network="polygon",
                           token="USDT", usdt=usdt, tx_hash=tx_hash, status="sending")
    try:
        w3.eth.send_raw_transaction(raw)
    except Exception as e:
        # UNKNOWN result: the tx may have been accepted but the response lost.
        # Leave status='sending' (tx_hash already persisted); do NOT mark failed.
        raise BroadcastUncertain(tx_hash, str(e)[:200]) from e
    storage.record_payment(ordem_id=ordem, pix_brl=pix_brl, chave=chave, network="polygon",
                           token="USDT", usdt=usdt, tx_hash=tx_hash, status="funding")
    return tx_hash


def poll_order(client: BackendClient, ordem: str, timeout: int = 300, network: str | None = None) -> dict:
    """Poll backend order status until resolved or timeout."""
    deadline = time.time() + timeout
    last = {"status": "pending"}
    while time.time() < deadline:
        try:
            st = client.order_status(ordem, network)
        except Exception as e:
            # timeout/erro de COMUNICACAO — resultado inconclusivo.
            # "err" eh classificado como falha DEFINITIVA pelo scheduler;
            # sem confirmacao do backend NAO ha classificacao financeira.
            last = {"status": "unknown", "msg": str(e)[:120]}
            break
        last = st
        if st.get("status") in ("sent", "done", "erro", "err"):
            break
        time.sleep(5)
    return last


def pay_pix(
    client: BackendClient,
    *,
    pix_value: float,
    chave: str,
    cupom: str | None = None,
    network: str = "polygon",
    on_order_created=None,
) -> dict:
    """Immediate PIX: create order -> fund -> poll. Returns final status.

    ``on_order_created(ordem_id)`` is called right after the order is created
    and BEFORE any funds move — the scheduler uses it to persist the explicit
    occurrence→order association for crash-safe recovery."""
    if network != "polygon":
        raise PaymentError(f"rede {network} ainda não habilitada para pagamento imediato")
    if not wallet.exists("polygon"):
        raise PaymentError("carteira Polygon não configurada")

    ordem = create_order(client, pix_value=pix_value, chave=chave, cupom=cupom,
                         extorno=wallet.address("polygon"), network=network)
    if on_order_created:
        on_order_created(ordem["ordem"])
    # pre-flight balance check: don't attempt a transfer that will certainly fail
    from .blockchain.polygon import get_chain
    chain = get_chain()
    addr = wallet.address("polygon")
    bal_usdt = chain.get_usdt_balance(addr)
    bal_native = chain.get_native_balance(addr)
    if bal_usdt < float(ordem["usdt"]):
        storage.record_payment(ordem_id=ordem["ordem"], pix_brl=pix_value, chave=chave,
                               network=network, token="USDT", usdt=ordem["usdt"], status="failed",
                               erro=f"saldo USDT insuficiente: {bal_usdt:.6f} < {ordem['usdt']}")
        raise PaymentError(f"saldo USDT insuficiente ({bal_usdt:.6f} USDT) — necessário {ordem['usdt']} USDT")
    if bal_native <= 0:
        storage.record_payment(ordem_id=ordem["ordem"], pix_brl=pix_value, chave=chave,
                               network=network, token="USDT", usdt=ordem["usdt"], status="failed",
                               erro="sem POL para pagar o gás da rede")
        raise PaymentError("carteira sem POL para pagar o gás — deposite POL para a taxa de rede")

    try:
        tx_hash = fund_order_polygon(deposit=ordem["deposit"], usdt=ordem["usdt"],
                                     ordem=ordem["ordem"], pix_brl=pix_value, chave=chave)
    except BroadcastUncertain as e:
        # Uncertain broadcast: the tx_hash is already persisted (status='sending').
        # Do NOT mark failed; return 'sending' so the caller reconciles by hash.
        return {"ordem": ordem["ordem"], "tx_hash": e.tx_hash,
                "status": "sending", "erro": e.detail}
    except Exception as e:
        # Proven no broadcast (e.g. sign/RPC failure BEFORE any signed tx was
        # persisted): safe to record a definitive failure.
        storage.record_payment(ordem_id=ordem["ordem"], pix_brl=pix_value, chave=chave,
                               network=network, token="USDT", usdt=ordem["usdt"],
                               status="failed", erro=str(e)[:200])
        raise PaymentError(f"falha ao enviar o depósito: {str(e)[:160]}") from e

    status = poll_order(client, ordem["ordem"], network=network)
    storage.record_payment(ordem_id=ordem["ordem"], pix_brl=pix_value, chave=chave,
                           network=network, token="USDT", usdt=ordem["usdt"],
                           cupom=ordem["cupom"], fee_pct=ordem["fee_pct"], tx_hash=tx_hash,
                           status=status.get("status"), resgate=status.get("resgate"),
                           erro=status.get("msg"))
    # Só campos públicos na resposta ao agente — o payload completo do backend
    # (que um dia pode crescer um campo interno) nunca vaza por aqui.
    _public = ("status", "pix", "pix_brl", "usdt", "resgate", "deposit",
               "expira", "erro", "msg", "txid", "quando", "cupom", "desconto")
    return {"ordem": ordem["ordem"], "tx_hash": tx_hash,
            **{k: v for k, v in status.items() if k in _public}}
