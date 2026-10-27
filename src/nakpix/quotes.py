"""Quote — sourced from the backend (single source of truth).

The MCP does NOT compute cost. It asks the server: "PIX de X BRL com cupom Y"
and the server returns the USDT needed. Internal cost (provider, base fee,
margin) lives ONLY on the server — the MCP never knows that formula.
"""

from __future__ import annotations

from .api import BackendClient


def get_quote(client: BackendClient, pix_brl: float, cupom: str = "", network: str | None = None) -> dict:
    """Backend /pix-quote -> campos de cliente (pix_brl, usdt, fee_pct, cambio, cupom, desconto)."""
    return client.pix_quote(pix_brl, cupom, network)
