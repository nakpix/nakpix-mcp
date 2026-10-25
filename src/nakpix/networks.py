"""Network constants — token contracts/mints, RPCs, symbols.

These are the OFFICIAL tokens. Never suggest depositing a token on a network
different from the one configured.
"""

from __future__ import annotations

import os

# ── Polygon ────────────────────────────────────────────────────────────────
POLYGON_USDT = "0xc2132d05d31c914a87c6611c10748aeb04b58e8f"  # USDT (bridged)
POLYGON_USDT_DECIMALS = 6
POLYGON_NATIVE = "POL"
POLYGON_RPCS = [
    os.environ.get("POLYGON_RPC_URL", "https://polygon-bor-rpc.publicnode.com"),
    "https://polygon.drpc.org",
]
POLYGON_CHAIN_ID = 137

# ── Solana ─────────────────────────────────────────────────────────────────
SOLANA_USDT_MINT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"  # USDT (Tether)
SOLANA_NATIVE = "SOL"
SOLANA_RPC = os.environ.get("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")

# SPL Token program + Associated Token Account program (for balance queries)
SOLANA_TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
SOLANA_ATA_PROGRAM = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"

NETWORKS = {
    "polygon": {
        "payment_token": "USDT",
        "payment_contract": POLYGON_USDT,
        "gas_token": POLYGON_NATIVE,
        "decimals": POLYGON_USDT_DECIMALS,
        "chain_id": POLYGON_CHAIN_ID,
    },
    "solana": {
        "payment_token": "USDT",
        "payment_mint": SOLANA_USDT_MINT,
        "gas_token": SOLANA_NATIVE,
    },
}
