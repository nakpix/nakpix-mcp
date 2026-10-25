"""PIX key parsing/validation tests."""

from __future__ import annotations

from nakpix.pix import key_type, validate_chave, extract_pix_from_text


def test_key_types():
    assert key_type("joao@email.com") == "email"
    assert key_type("123e4567-e89b-12d3-a456-426614174000") == "evp"
    assert key_type("+5511999999999") == "telefone"
    assert key_type("38318602897") == "cpf"
    assert key_type("12345678000199") == "cnpj"


def test_validate_rejects_garbage():
    assert not validate_chave("+555****4175")
    assert not validate_chave("abc")
    assert not validate_chave("a b c d")
    assert not validate_chave("12345")


def test_validate_accepts_good():
    assert validate_chave("joao@email.com")
    assert validate_chave("+5511999999999")
    assert validate_chave("38318602897")


def test_extract_from_text():
    r = extract_pix_from_text("pague R$ 149,90 para joao@email.com")
    assert r["chave"] == "joao@email.com"
    assert r["valor"] == 149.90
