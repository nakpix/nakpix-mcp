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
    """Validate against the backend (source of truth). Returns the fields the
    system needs: `codigo`, `pct` (fee — resolved server-side; the MCP does not
    compute cost) and `desconto` (what the user is told).

    The backend's commission `wallet` and usage count `usos` are INTERNAL and
    never surface — neither here nor in the agent-facing tool.
    """
    r = client.validate_coupon(codigo, network)
    if not r.get("ok"):
        raise ValueError(r.get("msg") or "cupom inválido")
    return {
        "codigo": r.get("codigo"),
        "pct": r.get("pct"),
        "desconto": r.get("desconto"),
    }


def effective_coupon(cupom: str | None) -> str:
    """Coupon a usar: o passado OU o default persistido. NAO resolve fee — o
    backend e a fonte de verdade do custo (o MCP nao sabe a formula)."""
    if cupom:
        return cupom.strip()
    return get_default_coupon() or ""
