"""15-minute unresolved-payment incident monitor.

Detects: crypto funds were sent (``tx_hash`` set) but the PIX is not confirmed
and no refund is confirmed, for longer than ``NAKPIX_INCIDENT_MINUTES``.

On detection (deduplicated by order id):
  1. create/open an incident
  2. enqueue a notification with the FULL operational data (subject + body)
     so the AGENT can send the email with its own email tool — the MCP never
     uses NAKPIX SMTP.

The monitor NEVER re-sends the transfer, never marks an unknown result as a
definitive failure, and never claims a refund happened without confirmation.
"""

from __future__ import annotations

import os
import time

from . import storage
from .api import BackendClient

INCIDENT_MINUTES = int(os.environ.get("NAKPIX_INCIDENT_MINUTES", "15"))
_TERMINAL = ("sent", "done", "solucionado", "refunded", "extornado", "resolved")
INCIDENT_EMAIL_TO = "suporte@nakswap.xyz"
MAX_EMAIL_ATTEMPTS = 3


def _is_unresolved(p: dict) -> bool:
    return bool(p.get("tx_hash")) and (p.get("status") or "") not in _TERMINAL


def build_incident_email(p: dict, order_status: dict) -> tuple[str, str]:
    import datetime

    def _ts(v) -> str:
        try:
            return datetime.datetime.fromtimestamp(int(v)).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return "—"

    agora = int(time.time())
    subject = (
        f"[NAKPIX ALERTA] Pix não concluído / Sem estorno / +{INCIDENT_MINUTES} minutos "
        f"— Transação {p.get('ordem_id') or p.get('tx_hash')}"
    )
    bstatus = order_status.get("status") or p.get("status")
    extornando = bstatus in ("extornando", "insuficiente")
    lines = [
        "NAKPIX — incidente de pendência financeira",
        "=" * 56,
        "",
        "[Identificação]",
        f"  ID da transação: {p.get('ordem_id') or '—'}",
        f"  ID do incidente: (ver sistema interno)",
        f"  Canal de origem: MCP",
        "",
        "[Horários]",
        f"  Criado em: {_ts(p.get('ts'))}",
        f"  Detectado em: {_ts(agora)}",
        f"  Limiar do alerta: {INCIDENT_MINUTES} minutos",
        "",
        "[Pagamento Pix]",
        f"  Valor BRL: {p.get('pix_brl')}",
        f"  Chave Pix: {p.get('chave') or '—'}",
        f"  Estado atual: {bstatus}",
        f"  Erro original: {order_status.get('erro') or p.get('erro') or '—'}",
        "",
        "[Blockchain]",
        f"  Rede: {p.get('network')}",
        f"  Token: {p.get('token') or 'USDT'}",
        f"  Valor enviado: {p.get('usdt')}",
        f"  Hash da transação: {p.get('tx_hash') or '—'}",
        f"  Endereço de depósito: {order_status.get('deposit') or '—'}",
        f"  Expira em: {_ts(order_status.get('expira'))}",
        "",
        "[Resgate]",
        f"  Código de resgate: {order_status.get('resgate') or p.get('resgate') or '—'}",
        "",
        "[Cotação e taxas]",
        f"  Fee %: {p.get('fee_pct')}",
        f"  Cupom: {p.get('cupom') or '—'}",
        "",
        "[Situação do estorno]",
        ("  Extorno em andamento (backend reporta 'extornando')."
         if extornando else
         "  Nenhum estorno confirmado até o momento."),
        f"  Estado backend: {bstatus}",
        "",
        "[Diagnóstico]",
        "  Fundos enviados (on-chain): sim (tx_hash presente)",
        "  Pix confirmado: " + ("sim" if bstatus in ("sent", "done") else "não"),
        "  Intervenção necessária: conciliar/estornar/confirmar manualmente",
    ]
    return subject, "\n".join(lines)


def scan_once(client: BackendClient | None = None) -> int:
    """One pass. Returns number of (new) incidents opened."""
    storage.init()
    client = client or BackendClient()
    created = 0
    cutoff = int(time.time() - INCIDENT_MINUTES * 60)
    # Page through ALL unresolved ops older than the threshold (oldest first),
    # with no hard cap — so no unresolved payment is ever missed.
    offset = 0
    while True:
        page = storage.list_unresolved_payments(cutoff, limit=500, offset=offset)
        if not page:
            break
        for p in page:
            if not _is_unresolved(p):
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
            # Dispatch the incident notification exactly once per in-flight
            # attempt: skip if a pending notification for this incident already
            # exists (a failed attempt leaves none behind), so a retry can never
            # queue a duplicate. The AGENT sends the email to
            # suporte@nakswap.xyz — the MCP never sends email.
            if (inc.get("email_state") == "pending" and not inc.get("email_ts")
                    and not storage.has_pending_incident_notification(inc["id"])):
                subject, body = build_incident_email(p, order_status)
                storage.update_incident(inc["id"], email_ts=int(time.time()))
                storage.enqueue_notification("agent", {
                    "kind": "incident",
                    "incident_id": inc["id"],
                    "to": INCIDENT_EMAIL_TO,
                    "ordem_id": ordem,
                    "status": order_status.get("status"),
                    "subject": subject,
                    "body": body,
                    "ts": int(time.time()),
                })
                created += 1
        offset += len(page)
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
