"""Default coupon + validation (persistent preference, backend-validated)."""

from __future__ import annotations

from .api import BackendClient
from .config import get_setting, set_setting


def set_default_coupon(codigo: str) -> None:
    set_setting("default_coupon", codigo.strip())


def get_default_coupon() -> str | None:
    return get_setting("default_coupon")


def remove_default_coupon() -> None:
    set_setting("default_coupon", None)


def validate_coupon(client: BackendClient, codigo: str, network: str | None = None) -> dict:
    """Validate against the backend (source of truth). Returns coupon info."""
    r = client.validate_coupon(codigo, network)
    if not r.get("ok"):
        raise ValueError(r.get("msg") or "cupom inválido")
    return {
        "codigo": r.get("codigo"),
        "pct": r.get("pct"),
        "wallet": r.get("wallet"),
        "usos": r.get("usos"),
    }


def resolve_fee(client: BackendClient, cupom: str | None, network: str | None = None) -> tuple[float, str]:
    """Return (fee_pct, effective_coupon). Auto-applies the persisted default
    coupon when none is passed; base fee if no/invalid coupon."""
    from .quotes import NAK_FEE_BASE_PCT

    if not cupom:
        cupom = get_default_coupon() or ""
    if not cupom:
        return NAK_FEE_BASE_PCT, ""
    try:
        info = validate_coupon(client, cupom, network)
        return float(info["pct"]), info["codigo"]
    except ValueError:
        return NAK_FEE_BASE_PCT, ""
