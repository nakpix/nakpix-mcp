"""PIX contact agenda (persistent, per install/identity)."""

from __future__ import annotations

import json
import os

from .config import ensure_dirs, NAKPIX_HOME

_CONTACTS_FILE = NAKPIX_HOME / "contacts" / "contacts.json"


def _load() -> dict:
    ensure_dirs()
    if _CONTACTS_FILE.exists():
        try:
            return json.loads(_CONTACTS_FILE.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save(data: dict) -> None:
    ensure_dirs()
    _CONTACTS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
    os.chmod(_CONTACTS_FILE, 0o600)


def _key(nome: str) -> str:
    return " ".join((nome or "").lower().split())


def add_contact(nome: str, chave: str, tipo: str | None = None) -> dict:
    data = _load()
    k = _key(nome)
    if not k or not chave:
        raise ValueError("nome e chave são obrigatórios")
    data[k] = {"nome": nome.strip(), "chave": chave.strip(), "tipo": tipo, "criado_em": _now()}
    _save(data)
    return data[k]


def list_contacts() -> list[dict]:
    return list(_load().values())


def get_contact(nome: str) -> dict | None:
    return _load().get(_key(nome))


def find_contact(nome: str) -> list[dict]:
    q = _key(nome)
    return [v for k, v in _load().items() if q in k]


def update_contact(nome: str, chave: str | None = None, tipo: str | None = None) -> dict | None:
    data = _load()
    k = _key(nome)
    if k not in data:
        return None
    if chave is not None:
        data[k]["chave"] = chave.strip()
    if tipo is not None:
        data[k]["tipo"] = tipo
    _save(data)
    return data[k]


def delete_contact(nome: str) -> bool:
    data = _load()
    k = _key(nome)
    if k not in data:
        return False
    del data[k]
    _save(data)
    return True


def _now() -> int:
    import time

    return int(time.time())
