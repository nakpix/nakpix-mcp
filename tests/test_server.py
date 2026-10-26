"""Server tool smoke tests (no real money, no network)."""

from __future__ import annotations

from nakpix import server


def test_wallet_status_empty(isolated_home):
    r = server.nakpix_wallet_status()
    assert r["has_wallet"] is False


def test_create_wallet_and_address(isolated_home):
    r = server.nakpix_create_wallet("polygon")
    assert r["status"] == "ok"
    assert r["address"].startswith("0x")
    st = server.nakpix_wallet_status()
    assert st["has_wallet"] is True


def test_create_wallet_no_secret_in_result(isolated_home):
    r = server.nakpix_create_wallet("solana")
    assert "mnemonic" not in r
    assert "private_key" not in r
    assert "passphrase" not in r


def test_validate_pix_tool():
    assert server.nakpix_validate_pix("joao@email.com")["valido"] is True
    assert server.nakpix_validate_pix("****")["valido"] is False


def test_pay_pix_blocked_without_confirm(isolated_home):
    server.nakpix_create_wallet("polygon")
    r = server.nakpix_pay_pix(pix_value=100, chave="joao@email.com", confirm=False)
    assert r["status"] == "blocked"


def test_pay_pix_invalid_key(isolated_home):
    server.nakpix_create_wallet("polygon")
    r = server.nakpix_pay_pix(pix_value=100, chave="****", confirm=True)
    assert r["status"] == "err"


def test_get_supported_networks():
    r = server.nakpix_get_supported_networks()
    assert "polygon" in r["networks"] and "solana" in r["networks"]


def test_contacts_tools(isolated_home):
    server.nakpix_add_contact("Pai", "+5511999999999")
    assert server.nakpix_get_contact("pai")["chave"] == "+5511999999999"
    assert len(server.nakpix_list_contacts()["contatos"]) == 1
    assert server.nakpix_add_contact("Mae", "****")["status"] == "err"  # invalid key


def test_confirmation_preference(isolated_home):
    assert server.nakpix_get_payment_confirmation()["payment_confirmation_enabled"] is True
    server.nakpix_set_payment_confirmation(False)
    assert server.nakpix_get_payment_confirmation()["payment_confirmation_enabled"] is False


def test_get_quote_no_internal_leak(isolated_home, fake_backend, monkeypatch):
    # A cotação NUNCA pode expor custo interno (o provedor), total bruto, nivel0
    # ou fee em R$ — só valor do PIX, fee do cupom, câmbio, USDT e cupom.
    monkeypatch.setattr(server, "_client", lambda: fake_backend)
    r = server.nakpix_get_quote(100.0, cupom="PARCEIRO10")
    assert r["status"] == "ok"
    for f in ("pix_brl", "fee_pct", "cambio", "usdt_necessario", "cupom"):
        assert f in r, f"faltou campo de cliente: {f}"
    for f in ("custo_o provedor_brl", "bruto_brl", "nivel0_brl", "fee_brl"):
        assert f not in r, f"vazou campo interno: {f}"
    assert r["fee_pct"] == 1.5
    assert r["cupom"] == "PARCEIRO10"
