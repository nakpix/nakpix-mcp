"""Wallet-agent security + functionality tests."""

from __future__ import annotations

import json
import os

import pytest

from nakpix.wallet_agent import crypto, manager, mnemonic, polygon, solana


def test_crypto_roundtrip_and_wrong_passphrase():
    env = crypto.encrypt_secret(b'{"mnemonic":"x"}', "senha")
    assert crypto.decrypt_secret(env, "senha") == b'{"mnemonic":"x"}'
    with pytest.raises(Exception):
        crypto.decrypt_secret(env, "errada")


def test_polygon_generate_roundtrip():
    p = polygon.generate()
    assert p["address"].startswith("0x") and len(p["address"]) == 42
    assert len(p["private_key"]) == 64
    assert len(p["mnemonic"].split()) == 12
    assert polygon.from_mnemonic(p["mnemonic"])["address"] == p["address"]


def test_solana_generate_roundtrip():
    s = solana.generate()
    assert len(s["address"]) in (43, 44)
    assert len(s["private_key"]) == 128
    assert solana.from_mnemonic(s["mnemonic"])["address"] == s["address"]


def test_mnemonic_valid():
    m = mnemonic.generate_mnemonic()
    assert mnemonic.is_valid_mnemonic(m)
    assert not mnemonic.is_valid_mnemonic("not a valid mnemonic at all")


def test_create_no_plaintext_secret_and_perms(isolated_home):
    info = manager.create("polygon")
    doc = json.loads(open(manager.wallet_path("polygon")).read())
    raw = json.dumps(doc)
    assert info["address"] not in ("", None)
    # the secret payload is encrypted; mnemonic/pk must NOT appear in the file
    secret = manager.reveal("polygon")
    assert secret["mnemonic"] not in raw
    assert secret["private_key"] not in raw
    assert "mnemonic" not in raw.lower() or "ciphertext" in raw  # envelope only
    # perms 0600
    mode = os.stat(manager.wallet_path("polygon")).st_mode & 0o777
    assert mode == 0o600


def test_create_two_wallets_share_passphrase(isolated_home):
    manager.create("polygon")
    manager.create("solana")
    # both decryptable with the single install passphrase
    r1 = manager.reveal("polygon")
    r2 = manager.reveal("solana")
    assert r1["mnemonic"] and r2["mnemonic"]


def test_create_refuses_overwrite(isolated_home):
    manager.create("polygon")
    with pytest.raises(manager.WalletError):
        manager.create("polygon")


def test_import_from_private_key(isolated_home):
    p = polygon.generate()
    info = manager.import_secret("polygon", p["private_key"])
    assert info["address"] == p["address"]


def test_import_backup_roundtrip(isolated_home):
    manager.create("polygon")
    exported = manager.export_backup("polygon")
    # wipe and restore into solana slot? no — restore same network after deleting
    os.remove(manager.wallet_path("polygon"))
    info = manager.import_backup("polygon", exported)
    assert info["address"] == exported["address"]


def test_wrong_network_rejected():
    with pytest.raises(manager.WalletError):
        manager.create("bitcoin")
