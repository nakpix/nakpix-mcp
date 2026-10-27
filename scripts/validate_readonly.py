import asyncio, os, sys, json
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ENV = {
    "NAKPIX_HOME": "/tmp/nakpix-val",
    "NAKPIX_BACKEND_URL": "https://nakpix.xyz/relay",
    "NAKPIX_BACKEND_SOLANA": "https://nakpix.xyz/relay-sol-prod",
    "PATH": os.environ["PATH"],
}

async def call(session, name, args=None):
    r = await session.call_tool(name, args or {})
    txt = r.content[0].text if r.content else "(vazio)"
    try:
        return json.loads(txt)
    except Exception:
        return txt

async def main():
    params = StdioServerParameters(command="nakpix-mcp", args=[], env=ENV)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()
            print("== 1) redes suportadas ==")
            print(json.dumps(await call(s, "nakpix_get_supported_networks"), ensure_ascii=False))
            print("\n== 2) wallet_status (antes) ==")
            print(json.dumps(await call(s, "nakpix_wallet_status"), ensure_ascii=False))
            print("\n== 3) cria carteira polygon (local) ==")
            print(json.dumps(await call(s, "nakpix_create_wallet", {"network": "polygon"}), ensure_ascii=False))
            import os as _os
            _cupom = _os.environ.get("NAKPIX_TEST_COUPON", "")
            if not _cupom:
                print("\n== 4) pulado: defina NAKPIX_TEST_COUPON p/ testar cupom ==")
            print(json.dumps(await call(s, "nakpix_validate_coupon", {"codigo": _cupom}), ensure_ascii=False)) if _cupom else None
            print(f"\n== 5) cotação R$100 c/ cupom (backend REAL) ==")
            print(json.dumps(await call(s, "nakpix_get_quote", {"pix_value": 100.0, "cupom": _cupom}), ensure_ascii=False)) if _cupom else None
            print("\n== 6) saldo on-chain (Polygon, carteira nova = 0) ==")
            print(json.dumps(await call(s, "nakpix_get_balances", {"network": "polygon"}), ensure_ascii=False))

asyncio.run(main())
