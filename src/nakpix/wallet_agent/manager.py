"""Wallet manager — orchestration of local encrypted wallets.

Security model
--------------
- The wallet file (``~/.nakpix/wallets/<network>.json``) holds ONLY an
  authenticated-encrypted envelope; no mnemonic/private key in plaintext.
- The envelope is decrypted with a passphrase derived via Argon2id.
- The passphrase lives in a separate OS-protected file (``0600`` in a ``0700``
  dir) or the ``NAKPIX_UNLOCK_PASSPHRASE`` env var — never in the wallet file,
  never in logs, never returned to the LLM.
- Recovery words are exposed ONLY through ``reveal()`` (the secure interface).

Public data (address, backed_up flag) is readable without the passphrase.
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

from ..config import ensure_dirs, NAKPIX_HOME
from . import crypto, mnemonic, polygon, solana

NETWORKS = ("polygon", "solana")
_MODULES = {"polygon": polygon, "solana": solana}
_WALLET_VERSION = 1


class WalletError(Exception):
    pass


def wallet_path(network: str) -> Path:
    _validate_network(network)
    return NAKPIX_HOME / "wallets" / f"{network}.json"


def _passphrase_path() -> Path:
    return NAKPIX_HOME / "wallets" / ".passphrase"


def _validate_network(network: str) -> None:
    if network not in NETWORKS:
        raise WalletError(f"rede desconhecida: {network} (use polygon ou solana)")


def get_passphrase() -> str:
    """Return the local unlock passphrase (env override, else protected file)."""
    env = os.environ.get("NAKPIX_UNLOCK_PASSPHRASE")
    if env:
        return env
    pp = _passphrase_path()
    if pp.exists():
        return pp.read_text("utf-8").strip()
    raise WalletError(
        "carteira não desbloqueada — defina NAKPIX_UNLOCK_PASSPHRASE "
        "ou desbloqueie via `nakpix-wallet-agent unlock`"
    )


def set_passphrase(passphrase: str) -> None:
    ensure_dirs()
    pp = _passphrase_path()
    pp.write_text(passphrase, "utf-8")
    os.chmod(pp, 0o600)


def _ensure_passphrase(passphrase: str | None = None) -> str:
    """Reuse the existing install passphrase if present, else generate one."""
    if passphrase:
        return passphrase
    try:
        return get_passphrase()
    except WalletError:
        p = secrets.token_urlsafe(32)
        set_passphrase(p)
        return p


def exists(network: str) -> bool:
    return wallet_path(network).exists()


def create(network: str, passphrase: str | None = None) -> dict:
    """Generate a wallet, encrypt and persist it. Returns public info only."""
    _validate_network(network)
    if exists(network):
        raise WalletError(f"carteira {network} já existe (use import/recover para trocar)")
    mod = _MODULES[network]
    secret = mod.generate()
    passphrase = _ensure_passphrase(passphrase)
    _save(network, secret, passphrase)
    return {"network": network, "address": secret["address"], "created": True}


def import_secret(network: str, secret: str, passphrase: str | None = None) -> dict:
    """Import an existing wallet from a mnemonic or private key."""
    _validate_network(network)
    if exists(network):
        raise WalletError(f"carteira {network} já existe — não vou sobrescrever")
    mod = _MODULES[network]
    secret = secret.strip()
    if mnemonic.is_valid_mnemonic(secret):
        payload = mod.from_mnemonic(secret)
    else:
        payload = mod.from_private_key(secret)
    passphrase = _ensure_passphrase(passphrase)
    _save(network, payload, passphrase)
    return {"network": network, "address": payload["address"], "imported": True}


def _save(network: str, payload: dict, passphrase: str) -> None:
    ensure_dirs()
    plaintext = json.dumps(
        {
            "mnemonic": payload.get("mnemonic"),
            "private_key": payload.get("private_key"),
            "derivation_path": payload.get("derivation_path"),
        }
    ).encode("utf-8")
    envelope = crypto.encrypt_secret(plaintext, passphrase)
    doc = {
        "version": _WALLET_VERSION,
        "network": network,
        "address": payload["address"],
        "encrypted_wallet": envelope,
        "backed_up": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    wp = wallet_path(network)
    wp.write_text(json.dumps(doc, indent=2, ensure_ascii=False), "utf-8")
    os.chmod(wp, 0o600)


def _read_doc(network: str) -> dict:
    wp = wallet_path(network)
    if not wp.exists():
        raise WalletError(f"carteira {network} não existe")
    return json.loads(wp.read_text("utf-8"))


def load(network: str, passphrase: str | None = None) -> dict:
    """Decrypt and return the secret payload (mnemonic + private key)."""
    doc = _read_doc(network)
    if passphrase is None:
        passphrase = get_passphrase()
    pt = crypto.decrypt_secret(doc["encrypted_wallet"], passphrase)
    return json.loads(pt.decode("utf-8"))


def address(network: str) -> str:
    """Public address — no decryption required."""
    return _read_doc(network)["address"]


def reveal(network: str, passphrase: str | None = None) -> dict:
    """SECURE INTERFACE ONLY: return recovery words + private key.

    Never call this from the MCP server. It exists for the local CLI so the
    owner can see/back up their recovery material.
    """
    secret = load(network, passphrase)
    return {
        "network": network,
        "address": address(network),
        "mnemonic": secret.get("mnemonic"),
        "private_key": secret.get("private_key"),
        "derivation_path": secret.get("derivation_path"),
    }


def backup_status(network: str) -> dict:
    doc = _read_doc(network)
    return {
        "network": network,
        "address": doc["address"],
        "backed_up": bool(doc.get("backed_up")),
        "created_at": doc.get("created_at"),
    }


def mark_backed_up(network: str) -> dict:
    doc = _read_doc(network)
    doc["backed_up"] = True
    wp = wallet_path(network)
    wp.write_text(json.dumps(doc, indent=2, ensure_ascii=False), "utf-8")
    os.chmod(wp, 0o600)
    return {"network": network, "backed_up": True}


def list_wallets() -> list[dict]:
    out = []
    for n in NETWORKS:
        if exists(n):
            doc = _read_doc(n)
            out.append(
                {
                    "network": n,
                    "address": doc["address"],
                    "backed_up": bool(doc.get("backed_up")),
                    "created_at": doc.get("created_at"),
                }
            )
    return out


def export_backup(network: str) -> dict:
    """Return the encrypted wallet document (safe to copy — already encrypted)."""
    return _read_doc(network)


def import_backup(network: str, doc: dict, passphrase: str | None = None) -> dict:
    """Restore from an exported encrypted backup JSON."""
    _validate_network(network)
    if exists(network):
        raise WalletError(f"carteira {network} já existe — não vou sobrescrever")
    if doc.get("network") != network or doc.get("version") != _WALLET_VERSION:
        raise WalletError("backup incompatível com esta rede/versão")
    if passphrase is None:
        passphrase = get_passphrase()
    # validate it decrypts before persisting
    crypto.decrypt_secret(doc["encrypted_wallet"], passphrase)
    wp = wallet_path(network)
    wp.write_text(json.dumps(doc, indent=2, ensure_ascii=False), "utf-8")
    os.chmod(wp, 0o600)
    return {"network": network, "address": doc["address"], "restored": True}


def sign_transaction(network: str, tx_payload, passphrase: str | None = None):
    """Sign an authorized transfer. Polygon: tx dict -> raw bytes.
    Solana: bytes message -> Signature. Returns network-appropriate result."""
    secret = load(network, passphrase)
    if network == "polygon":
        return polygon.sign_transaction(secret["private_key"], tx_payload)
    # solana: tx_payload is the message bytes
    from solders.keypair import Keypair

    kp = Keypair.from_bytes(bytes.fromhex(secret["private_key"]))
    return kp.sign_message(bytes(tx_payload))
