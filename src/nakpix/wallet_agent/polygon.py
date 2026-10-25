"""Polygon (EVM) wallet: generation, derivation, signing.

Derivation path ``m/44'/60'/0'/0/0`` (standard BIP44 for EVM chains, the same
path MetaMask uses on Polygon). Mnemonic is BIP39 (12 words).
"""

from __future__ import annotations

from eth_account import Account

DERIVATION_PATH = "m/44'/60'/0'/0/0"

Account.enable_unaudited_hdwallet_features()


def generate() -> dict:
    """Create a fresh wallet. Returns secret payload (never persist unencrypted)."""
    acct, mnemonic = Account.create_with_mnemonic()
    return {
        "mnemonic": mnemonic,
        "private_key": acct.key.hex(),
        "address": acct.address,
        "derivation_path": DERIVATION_PATH,
    }


def from_mnemonic(mnemonic: str) -> dict:
    acct = Account.from_mnemonic(" ".join(mnemonic.split()), account_path=DERIVATION_PATH)
    return {
        "mnemonic": " ".join(mnemonic.split()),
        "private_key": acct.key.hex(),
        "address": acct.address,
        "derivation_path": DERIVATION_PATH,
    }


def from_private_key(private_key: str) -> dict:
    acct = Account.from_key(private_key)
    return {"private_key": acct.key.hex(), "address": acct.address}


def address_from_private_key(private_key: str) -> str:
    return Account.from_key(private_key).address


def sign_transaction(private_key: str, tx_dict: dict) -> bytes:
    """Sign an already-constructed transaction dict; return raw signed bytes."""
    return Account.from_key(private_key).sign_transaction(tx_dict).raw_transaction
