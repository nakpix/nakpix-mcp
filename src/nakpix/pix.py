"""PIX key parsing and validation (no network calls — format checks)."""

from __future__ import annotations

import re

# email
_RE_EMAIL = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
# EVP (chave aleatória UUID)
_RE_EVP = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
# telefone +55 + 10/11 dígitos
_RE_TEL = re.compile(r"^\+55\d{10,11}$")
# cpf/cnpj/tel cru (10 a 14 dígitos)
_RE_NUM = re.compile(r"^\+?\d{10,14}$")


def key_type(chave: str) -> str | None:
    chave = (chave or "").strip()
    if _RE_EMAIL.fullmatch(chave):
        return "email"
    if _RE_EVP.fullmatch(chave):
        return "evp"
    if _RE_TEL.fullmatch(chave):
        return "telefone"
    if _RE_NUM.fullmatch(chave):
        n = chave.lstrip("+")
        if len(n) == 11:
            return "cpf"
        if len(n) == 14:
            return "cnpj"
        if len(n) in (10, 12, 13):
            return "telefone"
        return "numero"
    return None


def validate_chave(chave: str) -> bool:
    """True if the key is well-formed (CPF/CNPJ/tel+55/email/EVP)."""
    chave = (chave or "").strip()
    if re.search(r"[*<>;\"'\\]", chave) or len(chave.replace(" ", "")) < 6:
        return False
    return key_type(chave) is not None


def extract_pix_from_text(text: str) -> dict | None:
    """Best-effort: find a PIX key + BRL amount in free text (not authoritative)."""
    if not text:
        return None
    chave = None
    for m in re.finditer(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", text):
        chave = m.group(0)
        break
    if not chave:
        for m in re.finditer(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", text):
            chave = m.group(0)
            break
    if not chave:
        m = re.search(r"\+55\d{10,11}", text)
        if m:
            chave = m.group(0)
    valor = None
    m = re.search(r"R\$\s*([\d]{1,3}(?:\.\d{3})*(?:,\d{2})?)", text, re.IGNORECASE)
    if m:
        try:
            valor = float(m.group(1).replace(".", "").replace(",", "."))
        except ValueError:
            valor = None
    if not chave and not valor:
        return None
    return {"chave": chave, "valor": valor}
