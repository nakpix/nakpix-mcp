"""Quote + cost estimation.

Reuses the backend quote endpoint and 
the relay does (so the confirmation the agent shows matches the order the
backend will create). 

"""

from __future__ import annotations

from .api import BackendClient

NAK_FEE_BASE_PCT = 4.0
o provedor_COST_FIXED = 0.17  # R$
o provedor_COST_PCT = 0.01  # 1%


def cambio_from_quote(quote_data: dict, fallback_brl: float = 0.0) -> float:
    total_brl = float(quote_data.get("total_brl") or 0)
    total_usdt = float(quote_data.get("total_pxusdt") or 0)
    if total_usdt > 0:
        c = total_brl / total_usdt
        if c > 0:
            return c
    return fallback_brl * 5.0 if fallback_brl else 5.0


def estimate_cost(pix_brl: float, fee_pct: float, quote_data: dict) -> dict:
    """Compute the full cost breakdown for a PIX of `pix_brl` BRL."""
    cambio = cambio_from_quote(quote_data, pix_brl)
    nivel0 = pix_brl / (1 - fee_pct / 100.0) if fee_pct < 100 else pix_brl
    bruto = (nivel0 + o provedor_COST_FIXED) / (1 - o provedor_COST_PCT)
    usdt_nec = round(bruto / cambio, 6)
    return {
        "pix_brl": round(pix_brl, 2),
        "fee_pct": fee_pct,
        "cambio": round(cambio, 6),
        "usdt_necessario": usdt_nec,
        "nivel0_brl": round(nivel0, 2),
        "bruto_brl": round(bruto, 2),
        "custo_o provedor_brl": round(bruto - nivel0, 2),
        "fee_brl": round(nivel0 - pix_brl, 2),
    }


def get_quote(client: BackendClient, pix_brl: float, network: str | None = None) -> dict:
    r = client.quote(pix_brl, network)
    return r.get("quote") or r.get("data") or {}


def full_quote(client: BackendClient, pix_brl: float, fee_pct: float, network: str | None = None) -> dict:
    qd = get_quote(client, pix_brl, network)
    return estimate_cost(pix_brl, fee_pct, qd)
