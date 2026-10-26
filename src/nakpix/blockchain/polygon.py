"""Polygon balance / deposit queries (read-only, no signing)."""

from __future__ import annotations

from web3 import Web3

from ..networks import POLYGON_RPCS, POLYGON_USDT, POLYGON_USDT_DECIMALS

_USDT_ABI_BALANCE = {"constant": True, "inputs": [{"name": "_owner", "type": "address"}],
                     "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}],
                     "type": "function"}

_transfer_topic = Web3.keccak(text="Transfer(address,address,uint256)").hex()

# timeout curto por chamada RPC: estoura e troca de endpoint em vez de travar.
RPC_TIMEOUT = 10


class PolygonChain:
    def __init__(self, rpcs: list[str] | None = None):
        self._rpcs = rpcs or POLYGON_RPCS
        self._idx = 0
        self._w3 = self._new_w3(self._rpcs[0])

    def _new_w3(self, rpc: str) -> Web3:
        return Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": RPC_TIMEOUT}))

    def _w3_live(self) -> Web3:
        """Devolve um Web3 que responde DE VERDADE.

        FIX: ``HTTPProvider.is_connected()`` é sempre True, então um RPC
        lento/morto nunca era trocado. Agora testamos o bloco com timeout
        curto e rotacionamos pro próximo endpoint quando estourar.
        """
        for _ in range(len(self._rpcs)):
            w3 = self._new_w3(self._rpcs[self._idx])
            try:
                w3.eth.block_number
                self._w3 = w3
                return w3
            except Exception:
                self._idx = (self._idx + 1) % len(self._rpcs)
        return self._w3

    def _call(self, fn):
        """Executa ``fn(w3)`` com failover: troca de RPC a cada falha/timeout."""
        last: Exception | None = None
        for _ in range(len(self._rpcs)):
            w3 = self._new_w3(self._rpcs[self._idx])
            try:
                return fn(w3)
            except Exception as e:
                last = e
                self._idx = (self._idx + 1) % len(self._rpcs)
        if last is not None:
            raise last
        raise RuntimeError("nenhum RPC Polygon configurado")

    @staticmethod
    def _ck(addr: str) -> str:
        return Web3.to_checksum_address(addr)

    def get_tx_params(self, address: str):
        """nonce (pending) + gas_price com failover — seguros de re-tentar
        (ainda não assinamos nada)."""
        ck = self._ck(address)

        def _do(w3):
            nonce = w3.eth.get_transaction_count(ck, "pending")
            gas_price = w3.eth.gas_price
            return nonce, gas_price

        return self._call(_do)

    def get_usdt_balance(self, address: str) -> float:
        ck = self._ck(address)

        def _do(w3):
            contract = w3.eth.contract(address=self._ck(POLYGON_USDT),
                                       abi=[_USDT_ABI_BALANCE])
            raw = contract.functions.balanceOf(ck).call()
            return raw / (10 ** POLYGON_USDT_DECIMALS)

        return self._call(_do)

    def get_native_balance(self, address: str) -> float:
        ck = self._ck(address)
        return self._call(lambda w3: w3.eth.get_balance(ck) / 1e18)

    def get_balances(self, address: str) -> dict:
        return {
            "usdt": self.get_usdt_balance(address),
            "native": self.get_native_balance(address),
            "native_symbol": "POL",
        }

    def get_deposits(self, address: str, since_block: int | None = None,
                     max_blocks: int = 2000) -> list[dict]:
        """Recent incoming USDT transfers to `address`."""
        ck = self._ck(address)
        topic = "0x" + ck[2:].lower().rjust(64, "0")

        def _do(w3):
            latest = w3.eth.block_number
            if since_block is None:
                since = max(0, latest - max_blocks)
            else:
                since = since_block
            logs = w3.eth.get_logs({
                "address": self._ck(POLYGON_USDT),
                "fromBlock": since,
                "toBlock": "latest",
                "topics": [_transfer_topic, None, topic],
            })
            out = []
            for lg in logs:
                try:
                    value = int(lg["data"].hex(), 16) / (10 ** POLYGON_USDT_DECIMALS)
                except Exception:
                    value = 0.0
                out.append({
                    "tx_hash": lg["transactionHash"].hex(),
                    "block": lg["blockNumber"],
                    "from": "0x" + lg["topics"][1].hex()[-40:],
                    "usdt": value,
                })
            out.sort(key=lambda x: x["block"], reverse=True)
            return out

        return self._call(_do)

    def get_tx_state(self, tx_hash: str) -> str:
        """On-chain state of a broadcast transaction hash.

        Returns one of:
          - ``"mined"``    — in a block (funds definitively sent);
          - ``"pending"``  — in the mempool (not yet confirmed);
          - ``"notfound"`` — not on-chain and not in the mempool (definitively
            never broadcast / already dropped);
          - ``"unknown"``  — the RPC did not answer (cannot conclude).

        Used to reconcile an uncertain broadcast: only ``mined``/``notfound`` are
        conclusive; ``pending``/``unknown`` must be re-checked later.
        """
        for _ in range(len(self._rpcs)):
            w3 = self._new_w3(self._rpcs[self._idx])
            try:
                tx = w3.eth.get_transaction(tx_hash)
            except Exception:
                self._idx = (self._idx + 1) % len(self._rpcs)
                continue
            if tx is None:
                return "notfound"
            if tx.get("blockNumber") is not None:
                return "mined"
            return "pending"
        return "unknown"


_chain: PolygonChain | None = None


def get_chain() -> PolygonChain:
    global _chain
    if _chain is None:
        _chain = PolygonChain()
    return _chain
