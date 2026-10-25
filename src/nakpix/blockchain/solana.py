"""Solana balance / deposit queries via JSON-RPC (read-only, no signing)."""

from __future__ import annotations

import httpx

from ..networks import (
    SOLANA_RPC,
    SOLANA_TOKEN_PROGRAM,
    SOLANA_USDT_MINT,
)

_LAMPORTS = 1_000_000_000


def _rpc(method: str, params: list) -> dict:
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    r = httpx.post(SOLANA_RPC, json=body, timeout=30)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(data["error"].get("message", "rpc error"))
    return data["result"]


def get_sol_balance(address: str) -> float:
    try:
        lamports = _rpc("getBalance", [address])
        return lamports["value"] / _LAMPORTS
    except Exception:
        return 0.0


def get_usdt_balance(address: str) -> float:
    """USDT (SPL) balance by listing token accounts and matching the mint."""
    try:
        res = _rpc("getTokenAccountsByOwner", [
            address,
            {"programId": SOLANA_TOKEN_PROGRAM},
            {"encoding": "jsonParsed"},
        ])
        for acct in res.get("value", []):
            info = acct.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
            if info.get("mint") == SOLANA_USDT_MINT:
                amt = info.get("tokenAmount", {})
                return float(amt.get("uiAmount") or 0)
        return 0.0
    except Exception:
        return 0.0


def get_balances(address: str) -> dict:
    return {
        "usdt": get_usdt_balance(address),
        "native": get_sol_balance(address),
        "native_symbol": "SOL",
    }


def get_deposits(address: str, limit: int = 20) -> list[dict]:
    """Recent USDT transfers to `address` (best-effort; requires tx parsing)."""
    try:
        sigs = _rpc("getSignaturesForAddress", [
            address, {"limit": min(limit, 50)}
        ])
    except Exception:
        return []
    out = []
    for s in sigs:
        out.append({
            "signature": s.get("signature"),
            "slot": s.get("slot"),
            "err": s.get("err"),
            "block_time": s.get("blockTime"),
        })
    return out
