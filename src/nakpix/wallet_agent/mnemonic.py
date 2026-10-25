"""BIP39 mnemonic generation / validation (shared by Polygon and Solana)."""

from __future__ import annotations

from eth_account.hdaccount.mnemonic import Mnemonic

_m = Mnemonic()  # default Language.ENGLISH


def generate_mnemonic() -> str:
    """12-word BIP39 mnemonic (standard, interoperable)."""
    return _m.generate()


def is_valid_mnemonic(words: str) -> bool:
    try:
        return bool(words) and _m.is_mnemonic_valid(" ".join(words.split()))
    except Exception:
        return False
