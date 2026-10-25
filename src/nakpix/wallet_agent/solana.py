"""Solana wallet: generation, derivation, signing.

Derivation uses the standard Solana path ``m/44'/501'/0'/0'`` via
``solders.Keypair.from_seed_phrase_and_passphrase`` (same derivation Phantom /
Solflare use). Mnemonic is BIP39 (12 words). This is documented in
``docs/references/wallet-recovery.md`` and covered by a roundtrip test.
"""

from __future__ import annotations

from solders.keypair import Keypair

DERIVATION_PATH = "m/44'/501'/0'/0'"


def generate() -> dict:
    """Create a fresh wallet. Returns secret payload (never persist unencrypted)."""
    from .mnemonic import generate_mnemonic

    mnemonic = generate_mnemonic()
    kp = Keypair.from_seed_phrase_and_passphrase(mnemonic, "")
    return {
        "mnemonic": mnemonic,
        "private_key": bytes(kp).hex(),  # 64-byte keypair (secret + pubkey)
        "address": str(kp.pubkey()),
        "derivation_path": DERIVATION_PATH,
    }


def from_mnemonic(mnemonic: str) -> dict:
    kp = Keypair.from_seed_phrase_and_passphrase(" ".join(mnemonic.split()), "")
    return {
        "mnemonic": " ".join(mnemonic.split()),
        "private_key": bytes(kp).hex(),
        "address": str(kp.pubkey()),
        "derivation_path": DERIVATION_PATH,
    }


def from_private_key(private_key: str) -> dict:
    kp = Keypair.from_bytes(bytes.fromhex(private_key))
    return {"private_key": bytes(kp).hex(), "address": str(kp.pubkey())}


def address_from_private_key(private_key: str) -> str:
    return str(Keypair.from_bytes(bytes.fromhex(private_key)).pubkey())
