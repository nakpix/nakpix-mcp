"""Polygon balance / deposit queries (read-only, no signing)."""

from __future__ import annotations

from web3 import Web3

from ..networks import POLYGON_RPCS, POLYGON_USDT, POLYGON_USDT_DECIMALS

_USDT_ABI_BALANCE = {"constant": True, "inputs": [{"name": "_owner", "type": "address"}],
                     "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}],
                     "type": "function"}

_transfer_topic = Web3.keccak(text="Transfer(address,address,uint256)").hex()


class PolygonChain:
    def __init__(self, rpcs: list[str] | None = None):
        self._rpcs = rpcs or POLYGON_RPCS
        self._idx = 0
        self._w3 = Web3(Web3.HTTPProvider(self._rpcs[0]))

    def _w3_live(self):
        last = None
        for _ in range(len(self._rpcs)):
            if self._w3.is_connected():
                return self._w3
            self._idx = (self._idx + 1) % len(self._rpcs)
            self._w3 = Web3(Web3.HTTPProvider(self._rpcs[self._idx]))
        return self._w3

    @staticmethod
    def _ck(addr: str) -> str:
        return Web3.to_checksum_address(addr)

    def get_usdt_balance(self, address: str) -> float:
        w3 = self._w3_live()
        contract = w3.eth.contract(address=self._ck(POLYGON_USDT), abi=[_USDT_ABI_BALANCE])
        raw = contract.functions.balanceOf(self._ck(address)).call()
        return raw / (10 ** POLYGON_USDT_DECIMALS)

    def get_native_balance(self, address: str) -> float:
        w3 = self._w3_live()
        return w3.eth.get_balance(self._ck(address)) / 1e18

    def get_balances(self, address: str) -> dict:
        return {
            "usdt": self.get_usdt_balance(address),
            "native": self.get_native_balance(address),
            "native_symbol": "POL",
        }

    def get_deposits(self, address: str, since_block: int | None = None, max_blocks: int = 2000) -> list[dict]:
        """Recent incoming USDT transfers to `address`."""
        w3 = self._w3_live()
        ck = self._ck(address)
        latest = w3.eth.block_number
        if since_block is None:
            since_block = max(0, latest - max_blocks)
        logs = w3.eth.get_logs({
            "address": self._ck(POLYGON_USDT),
            "fromBlock": since_block,
            "toBlock": "latest",
            "topics": [_transfer_topic, None, "0x" + ck[2:].lower().rjust(64, "0")],
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


_chain: PolygonChain | None = None


def get_chain() -> PolygonChain:
    global _chain
    if _chain is None:
        _chain = PolygonChain()
    return _chain
