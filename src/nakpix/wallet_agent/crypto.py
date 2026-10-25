"""Authenticated encryption for wallet backups (Argon2id + AES-256-GCM).

The envelope stores the KDF parameters so backups are versioned and portable.
The passphrase is the human unlock secret and is NEVER persisted — not in the
file, not in logs, not in chat.
"""

from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

FORMAT = "NAKPIX_ENCRYPTED_WALLET_V1"
_KDF_DEFAULT = {
    "name": "argon2id",
    "time_cost": 3,
    "memory_cost": 64 * 1024,  # 64 MiB
    "parallelism": 1,
}


def _derive_key(passphrase: str, salt: bytes, params: dict) -> bytes:
    # cryptography >=45 renamed ``parallelism`` to ``lanes``; support both.
    lanes = int(params.get("parallelism", _KDF_DEFAULT["parallelism"]))
    kw = dict(
        salt=salt,
        length=32,
        iterations=int(params.get("time_cost", _KDF_DEFAULT["time_cost"])),
        memory_cost=int(params.get("memory_cost", _KDF_DEFAULT["memory_cost"])),
    )
    try:
        kdf = Argon2id(lanes=lanes, **kw)
    except TypeError:
        kdf = Argon2id(parallelism=lanes, **kw)
    return kdf.derive(passphrase.encode("utf-8"))


def encrypt_secret(plaintext: bytes, passphrase: str) -> dict:
    salt = os.urandom(16)
    nonce = os.urandom(12)
    key = _derive_key(passphrase, salt, _KDF_DEFAULT)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, None)
    return {
        "format": FORMAT,
        "ciphertext": base64.b64encode(ciphertext).decode(),
        "nonce": base64.b64encode(nonce).decode(),
        "kdf": {
            "name": _KDF_DEFAULT["name"],
            "salt": base64.b64encode(salt).decode(),
            "time_cost": _KDF_DEFAULT["time_cost"],
            "memory_cost": _KDF_DEFAULT["memory_cost"],
            "parallelism": _KDF_DEFAULT["parallelism"],
        },
    }


def decrypt_secret(envelope: dict, passphrase: str) -> bytes:
    if not isinstance(envelope, dict) or envelope.get("format") != FORMAT:
        raise ValueError("formato de backup desconhecido/inaplicável")
    salt = base64.b64decode(envelope["kdf"]["salt"])
    nonce = base64.b64decode(envelope["nonce"])
    ciphertext = base64.b64decode(envelope["ciphertext"])
    key = _derive_key(passphrase, salt, envelope["kdf"])
    return AESGCM(key).decrypt(nonce, ciphertext, None)
