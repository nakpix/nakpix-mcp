"""Configuration, paths and the pseudonymous technical identity.

Everything lives under ``NAKPIX_HOME`` (default ``~/.nakpix``):

    ~/.nakpix/
    ├── config.json      # identity + defaults (no secrets)
    ├── wallets/         # encrypted wallet files (polygon.json / solana.json)
    ├── contacts/        # PIX contact agenda (per identity)
    ├── settings/        # preferences (confirmation, default coupon)
    ├── nakpix.db        # SQLite: scheduler, history, incidents
    └── logs/            # structured logs (no secrets)

The identity is a random, pseudonymous technical id (``client_id``) that
separates wallets, contacts, coupons, settings and history between
installations. It is NOT a login and NOT a private key.
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

NAKPIX_HOME = Path(os.environ.get("NAKPIX_HOME") or Path.home() / ".nakpix")


def home() -> Path:
    return NAKPIX_HOME


def ensure_dirs() -> dict[str, Path]:
    dirs = {
        "root": NAKPIX_HOME,
        "wallets": NAKPIX_HOME / "wallets",
        "contacts": NAKPIX_HOME / "contacts",
        "settings": NAKPIX_HOME / "settings",
        "logs": NAKPIX_HOME / "logs",
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass
    return dirs


def config_path() -> Path:
    return NAKPIX_HOME / "config.json"


def _load_config() -> dict:
    p = config_path()
    if p.exists():
        try:
            return json.loads(p.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_config(cfg: dict) -> None:
    ensure_dirs()
    config_path().write_text(json.dumps(cfg, indent=2, ensure_ascii=False), "utf-8")
    try:
        os.chmod(config_path(), 0o600)
    except OSError:
        pass


def get_client_id() -> str:
    """Return the pseudonymous technical identity, creating it on first use."""
    cfg = _load_config()
    cid = cfg.get("client_id")
    if not cid:
        cid = "nkp_" + secrets.token_hex(16)
        cfg["client_id"] = cid
        _save_config(cfg)
    return cid


def get_config() -> dict:
    cfg = _load_config()
    cfg.setdefault("client_id", get_client_id())
    return cfg


def set_config(**fields) -> dict:
    cfg = _load_config()
    cfg.update(fields)
    _save_config(cfg)
    return cfg


def get_setting(key: str, default=None):
    """Read a persisted user preference (settings.json)."""
    ensure_dirs()
    sp = NAKPIX_HOME / "settings" / "settings.json"
    if sp.exists():
        try:
            data = json.loads(sp.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
    else:
        data = {}
    return data.get(key, default)


def set_setting(key: str, value) -> None:
    ensure_dirs()
    sp = NAKPIX_HOME / "settings" / "settings.json"
    data = {}
    if sp.exists():
        try:
            data = json.loads(sp.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
    data[key] = value
    sp.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
    try:
        os.chmod(sp, 0o600)
    except OSError:
        pass


def backend_url() -> str:
    return os.environ.get("NAKPIX_BACKEND_URL", "").rstrip("/")


def backend_url_for(network: str) -> str:
    key = {"polygon": "NAKPIX_BACKEND_POLYGON", "solana": "NAKPIX_BACKEND_SOLANA"}.get(network)
    if key:
        v = os.environ.get(key, "").rstrip("/")
        if v:
            return v
    return backend_url()
