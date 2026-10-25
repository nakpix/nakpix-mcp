"""15-minute unresolved-payment incident monitor.

Detects: crypto funds were sent (``tx_hash`` set) but the PIX is not confirmed
and no refund is confirmed, for longer than ``NAKPIX_INCIDENT_MINUTES``.

On detection (deduplicated by order id):
  1. create/open an incident
  2. email suporte@nakswap.xyz with all operational data (no secrets)
  3. enqueue a user notification (delivered by the agent when it reconnects)

The monitor NEVER re-sends the transfer, never marks an unknown result as a
definitive failure, and never claims a refund happened without confirmation.
"""

from __future__ import annotations

import os
import time

from . import email as email_mod
from . import storage
from .api import BackendClient

INCIDENT_MINUTES = int(os.environ.get("NAKPIX_INCIDENT_MINUTES", "15"))
_TERMINAL = ("sent", "done", "solucionado", "refunded", "extornado", "resolved")


def _is_unresolved(p: dict) -> bool:
    return bool(p.get("tx_hash")) and (p.get("status") or "") not in _TERMINAL


def build_incident_email(p: dict, order_status: dict) -> tuple[str, str]:
    subject = (
        f"[NAKPIX ALERTA] Pix não concluído / Sem estorno / +{INCIDENT_MINUTES} minutos "
        f"— Transação {p.get('ordem_id') or p.get('tx_hash')}"
    )
    lines = [
        "NAKPIX — incidente de pendência financeira",
        "=" * 56,
        "",
        "[Identificação]",
        f"  ID da transação: {p.get('ordem_id') or '—'}",
        f"  ID do incidente: (ver sistema interno)",
        f"  Canal de origem: MCP",
        "",
        "[Pagamento Pix]",
        f"  Valor BRL: {p.get('pix_brl')}",
        f"  Chave Pix: {p.get('chave') or '—'}",
        f"  Estado atual: {order_status.get('status') or p.get('status')}",
        f"  Erro original: {order_status.get('erro') or p.get('erro') or '—'}",
        "",
        "[Blockchain]",
        f"  Rede: {p.get('network')}",
        f"  Token: {p.get('token') or 'USDT'}",
        f"  Valor enviado: {p.get('usdt')}",
        f"  Hash da transação: {p.get('tx_hash') or '—'}",
        "",
        "[Cotação e taxas]",
        f"  Fee %: {p.get('fee_pct')}",
        f"  Cupom: {p.get('cupom') or '—'}",
        "",
        "[Diagnóstico]",
        "  Fundos enviados (on-chain): sim (tx_hash presente)",
        "  Pix confirmado: " + ("sim" if order_status.get("status") in ("sent", "done") else "não"),
        "  Estorno confirmado: não (não há confirmação)",
        "  Intervenção necessária: conciliar/estornar/confirmar manualmente",
    ]
    return subject, "\n".join(lines)


def scan_once(client: BackendClient | None = None) -> int:
    """One pass. Returns number of (new) incidents opened."""
    storage.init()
    client = client or BackendClient()
    created = 0
    cutoff = time.time() - INCIDENT_MINUTES * 60
    for p in storage.list_payments(limit=500):
        if not _is_unresolved(p):
            continue
        if int(p.get("ts") or 0) > cutoff:
            continue
        ordem = p.get("ordem_id")
        order_status: dict = {}
        if ordem:
            try:
                order_status = client.order_status(ordem, p.get("network"))
            except Exception as e:
                order_status = {"status": "erro", "erro": str(e)[:120]}
        # if the backend says it resolved, close any open incident
        if order_status.get("status") in _TERMINAL:
            _close_by_ordem(ordem)
            continue
        inc = storage.create_incident(dedup_key=ordem or p.get("tx_hash") or str(p.get("id")),
                                      ordem_id=ordem)
        if inc.get("email_sent") == 0:
            subject, body = build_incident_email(p, order_status)
            ok = email_mod.send_email(subject, body)
            storage.update_incident(inc["id"], email_sent=1 if ok else 0,
                                    email_ts=int(time.time()) if ok else 0)
            storage.enqueue_notification("agent", {
                "kind": "incident",
                "ordem_id": ordem,
                "status": order_status.get("status"),
                "email_sent": ok,
                "ts": int(time.time()),
            })
            created += 1
    return created


def _close_by_ordem(ordem: str | None) -> None:
    if not ordem:
        return
    with storage.conn() as c:
        rows = c.execute("SELECT id FROM incidents WHERE ordem_id=? AND status='open'", (ordem,)).fetchall()
    for r in rows:
        storage.update_incident(r["id"], status="resolved", resolvido_ts=int(time.time()))


def run_loop(interval: int = 60) -> None:
    client = BackendClient()
    while True:
        try:
            scan_once(client)
        except Exception as e:
            print(f"[incidents] erro: {str(e)[:120]}", flush=True)
        time.sleep(interval)


def main() -> None:
    print(f"[incidents] monitor ON — threshold {INCIDENT_MINUTES}min", flush=True)
    run_loop()
