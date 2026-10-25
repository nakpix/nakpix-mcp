"""HTTP client for the existing NAKPIX backend.

The MCP reuses the backend's processing — it NEVER reimplements order
creation, quoting, coupon validation or PIX execution. All money-moving
calls are read/write through these endpoints only.
"""

from __future__ import annotations

import httpx

from ..config import backend_url_for

DEFAULT_TIMEOUT = 25.0


class BackendError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.message = message
        self.status = status


class BackendClient:
    def __init__(self, base_url: str | None = None, timeout: float = DEFAULT_TIMEOUT):
        self.base_url = (base_url or "").rstrip("/")
        self.timeout = timeout
        self._client = httpx.Client(timeout=timeout, follow_redirects=False)

    def url(self, path: str, network: str | None = None) -> str:
        # explicit base_url wins; otherwise resolve from env (per-network or default)
        base = self.base_url or backend_url_for(network)
        return f"{base}{path}"

    def _get(self, path: str, network: str | None = None, **params):
        r = self._client.get(self.url(path, network), params=params)
        return self._handle(r)

    def _post(self, path: str, body: dict, network: str | None = None, **params):
        r = self._client.post(self.url(path, network), json=body, params=params)
        return self._handle(r)

    @staticmethod
    def _handle(r: httpx.Response) -> dict:
        try:
            data = r.json()
        except Exception:
            data = {"status": "err", "msg": f"resposta não-JSON ({r.status_code})"}
        if r.status_code >= 400 or data.get("status") in ("err", "error"):
            raise BackendError(data.get("msg") or f"HTTP {r.status_code}", r.status_code)
        return data

    # ---- quotes / coupons / status ----
    def quote(self, brl: float, network: str | None = None) -> dict:
        return self._get("/pix-quote-brl", network, brl=f"{brl:.2f}")

    def validate_coupon(self, codigo: str, network: str | None = None) -> dict:
        return self._get("/cupom-check", network, c=codigo)

    def order_status(self, ordem: str, network: str | None = None) -> dict:
        return self._get("/pix-status", network, ordem=ordem)

    def web3_status(self, ordem: str, network: str | None = None) -> dict:
        return self._get("/pix-web3-status", network, ordem=ordem)

    def favorecido(self, key: str, network: str | None = None) -> dict:
        return self._get("/favorecido", network, key=key)

    def health(self, network: str | None = None) -> dict:
        return self._get("/health", network)

    # ---- order creation ----
    def create_order(
        self,
        *,
        pix_value: float,
        chave: str,
        cupom: str = "",
        fee: float | None = None,
        extorno: str = "",
        brcode: str = "",
        network: str | None = None,
    ) -> dict:
        return self._post(
            "/pix-order",
            {
                "pix_value": pix_value,
                "chave": chave,
                "cupom": cupom,
                "fee": fee,
                "extorno": extorno,
                "brcode": brcode,
            },
            network,
        )

    def prepare_web3(
        self,
        *,
        pix_value: float,
        chave: str,
        extorno: str,
        cupom: str = "",
        usdt: float = 0.0,
        brcode: str = "",
        network: str | None = None,
    ) -> dict:
        return self._post(
            "/pix-web3-prepare",
            {
                "pix_value": pix_value,
                "chave": chave,
                "extorno": extorno,
                "cupom": cupom,
                "usdt": usdt,
                "brcode": brcode,
            },
            network,
        )

    def resolve_qr(self, brcode: str, network: str | None = None) -> dict:
        return self._post("/pix-qr-resolve", {"brcode": brcode}, network)

    def receipt_url(self, *, code: str, txid: str, name: str, valor: str, network: str | None = None) -> str:
        from urllib.parse import urlencode

        qs = urlencode({"code": code, "txid": txid, "name": name, "valor": valor})
        return f"{self.url('/comprovante', network)}?{qs}"

    def close(self) -> None:
        self._client.close()
