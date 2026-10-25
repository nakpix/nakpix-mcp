"""Persistent scheduler + worker for PIX payments (one-shot and recurring).

Runs as a background process (``nakpix-scheduler``) independent of any active
agent conversation. Execution is idempotent via the occurrences table.

Confirmation happens at SCHEDULE CREATION time — never at execution.
"""

from __future__ import annotations

import datetime
import time

from .. import storage
from .. import payments as payments_mod
from ..api import BackendClient


def next_occurrence(schedule: dict, from_ts: int) -> int | None:
    rec = schedule.get("recurrencia", "once")
    if rec == "once":
        return None
    dt = datetime.datetime.fromtimestamp(from_ts)
    if rec == "daily":
        nxt = dt + datetime.timedelta(days=1)
    elif rec == "weekly":
        nxt = dt + datetime.timedelta(days=7)
    elif rec == "monthly":
        nxt = _add_months(dt, 1, schedule.get("dia_ancora"))
    elif rec == "yearly":
        nxt = _add_months(dt, 12, schedule.get("dia_ancora"))
    elif rec == "interval":
        nxt = dt + datetime.timedelta(days=int(schedule.get("intervalo_dias") or 1))
    else:
        return None
    if schedule.get("data_fim") and nxt.timestamp() > schedule["data_fim"]:
        return None
    reps = int(schedule.get("reps_executadas") or 0)
    max_reps = schedule.get("max_reps")
    if max_reps and reps + 1 >= max_reps:
        return None
    return int(nxt.timestamp())


def _add_months(dt: datetime.datetime, months: int, anchor_day: int | None = None) -> datetime.datetime:
    m = dt.month - 1 + months
    year = dt.year + m // 12
    month = m % 12 + 1
    # anchor_day keeps the ORIGINAL day-of-month (e.g. 31) across shorter
    # months, so Jan-31 -> Feb-28 -> Mar-31 (not stuck at 28 forever).
    day = min(anchor_day or dt.day, _days_in_month(year, month))
    return dt.replace(year=year, month=month, day=day)


def _days_in_month(year: int, month: int) -> int:
    import calendar

    return calendar.monthrange(year, month)[1]


def execute_schedule(schedule: dict, client: BackendClient,
                     on_order_created=None) -> dict:
    """Execute one due schedule occurrence (respects reminder_only).

    ``on_order_created(ordem_id)`` is invoked the moment the order is created
    and BEFORE any funds move, so the caller can persist the explicit
    occurrence→order association (crash-safe recovery relies on it)."""
    if schedule.get("reminder_only"):
        storage.enqueue_notification("agent", {
            "kind": "reminder",
            "nome": schedule["nome"],
            "valor_brl": schedule["valor_brl"],
            "chave": schedule["chave"],
            "ts": int(time.time()),
        })
        return {"status": "reminder"}

    result = payments_mod.pay_pix(
        client,
        pix_value=schedule["valor_brl"],
        chave=schedule["chave"],
        cupom=schedule.get("cupom"),
        network=schedule.get("network") or "polygon",
        on_order_created=on_order_created,
    )
    return {"status": result.get("status"), "ordem": result.get("ordem"),
            "erro": result.get("erro") or result.get("msg"),
            "resgate": result.get("resgate"),
            "tx_hash": result.get("tx_hash")}


_DONE_STATUS = ("sent", "done", "solucionado", "resolved")
_REFUNDED_STATUS = ("refunded", "extornado")
# Definitive backend failures — NOT 'notfound' (an order the backend cannot
# locate yet is inconclusive, not proof of no transfer).
_FAILED_STATUS = ("erro", "err", "failed")
# Broadcast attempted but the result is UNKNOWN (RPC timeout / lost response).
_UNKNOWN_STATUS = ("sending", "unknown")

# Backend order statuses that prove the funds moved (or are moving) — a deposit
# arrived or the PIX is in flight. Used to reconcile a crash whose broadcast was
# accepted but never recorded locally.
_MOVED_STATUS = ("funding", "sent", "done", "solucionado", "resolved",
                 "refunded", "extornado", "pending", "processing")

# Backend order statuses that PROVE no transfer happened (order definitively
# expired / cancelled). Distinct from the mere ABSENCE of a local record, and
# deliberately EXCLUDING 'notfound'/'inexistente' — an order the backend cannot
# locate yet is NOT proof that no funds were sent (it may be transient).
_NO_FUNDS_STATUS = ("expirada", "dead", "cancelada", "cancelado", "cancelled")


def _occurrence_status(result_status: str | None) -> str:
    """Map a pay_pix result status to an occurrence status.

    A 'pending' PIX (funds sent, still processing) must NOT be recorded as
    'done' — it is tracked to completion by the incident monitor. A refund
    ('refunded'/'extornado') is recorded as its own 'refunded' state — it is
    NOT a successful PIX and must never read as one. An uncertain broadcast
    ('sending'/'unknown') is recorded as 'investigacao' — never 'failed'.
    """
    if result_status in _DONE_STATUS:
        return "done"
    if result_status in _REFUNDED_STATUS:
        return "refunded"
    if result_status in _FAILED_STATUS:
        return "failed"
    if result_status in _UNKNOWN_STATUS:
        return "investigacao"
    return "pending"


STALE_RUNNING_SECONDS = 600   # poll timeout (300s) + safety buffer


def _advance_schedule(s: dict, due_ts: int) -> None:
    """Advance a schedule past ``due_ts`` after an execution (or recovery)."""
    if not s or s.get("status") != "active":
        return
    nxt = next_occurrence(s, due_ts)
    reps = int(s.get("reps_executadas") or 0) + 1
    if nxt is None:
        storage.update_schedule(s["id"], status="done", reps_executadas=reps)
    else:
        storage.update_schedule(s["id"], proxima_ocorrencia=nxt, reps_executadas=reps)


def _tx_state(tx_hash: str) -> str:
    """On-chain state of a broadcast hash: 'mined'|'pending'|'notfound'|'unknown'.

    Thin wrapper around the blockchain client so tests can monkeypatch it.
    """
    from .blockchain.polygon import get_chain

    return get_chain().get_tx_state(tx_hash)


def recover_stale_running(now: int | None = None, client: BackendClient | None = None) -> int:
    """Recover occurrences left ``running`` by a crashed previous run.

    Only occurrences past the execution deadline are considered (a recent
    ``running`` row may still belong to a live worker). Recovery uses ONLY the
    explicit occurrence→order association recorded before funds moved — never a
    key+value+network guess (which can collide with an unrelated manual
    payment):

    - no ``ordem_id`` on the occurrence → no order was ever created, so no
      money moved: keep it in ``investigacao``, do NOT advance and do NOT
      re-send;
    - ``ordem_id`` present + payment record with ``tx_hash`` → the transfer was
      broadcast and recorded: recover its status/resgate/hash and advance the
      schedule (the incident monitor keeps tracking it if still pending);
    - ``ordem_id`` present + payment record without ``tx_hash`` → funding never
      broadcast (e.g. insufficient balance): mark failed and advance;
    - ``ordem_id`` present + no local payment → the transfer may or may not
      have happened: ask the backend by the explicit order id. If it confirms
      funds moved → reconcile + advance; if it PROVES no transfer
      (expirada/notfound/cancelled) → failed + advance; if it is unreachable or
      inconclusive → keep ``investigacao``, do NOT advance and do NOT conclude
      (absence of a local record is not proof of no transfer).
    """
    storage.init()
    now = now or int(time.time())
    deadline = now - STALE_RUNNING_SECONDS
    recovered = 0
    with storage.conn() as c:
        rows = c.execute(
            "SELECT * FROM occurrences WHERE status='running' AND executed_ts < ?",
            (deadline,),
        ).fetchall()
    for r in rows:
        s = storage.get_schedule(r["schedule_id"])
        ordem_id = r["ordem_id"]
        if not ordem_id:
            # No order created → no funds moved. Never guess a payment by
            # key+value. Manual investigation, do NOT advance / re-send.
            storage.update_occurrence(
                r["id"], status="investigacao",
                erro="recuperação: sem ordem criada (sem evidência de transferência)",
            )
            recovered += 1
            continue

        payment = storage.get_payment(ordem_id)
        if payment and payment.get("tx_hash"):
            occ_status = _occurrence_status(payment.get("status"))
            storage.update_occurrence(
                r["id"], status=occ_status, ordem_id=ordem_id,
                tx_hash=payment.get("tx_hash"),
                resgate=payment.get("resgate"),
                erro=payment.get("erro") if occ_status == "failed" else None,
            )
            # An uncertain broadcast ('sending') maps to 'investigacao': do NOT
            # advance the schedule until the blockchain is consulted by hash.
            if occ_status != "investigacao":
                _advance_schedule(s, r["due_ts"])
        elif payment and not payment.get("tx_hash"):
            storage.update_occurrence(
                r["id"], status="failed", ordem_id=ordem_id,
                erro=payment.get("erro") or "financiamento não transmitido",
            )
            _advance_schedule(s, r["due_ts"])
        else:
            # Order created but no LOCAL payment record (no tx_hash). The
            # transfer may or may not have happened — ask the backend by the
            # EXPLICIT order id (never by key+value). Three distinct outcomes:
            #   (a) backend confirms funds moved → reconcile + advance;
            #   (b) backend PROVES no transfer (expired/cancelled/notfound) →
            #       failed + advance;
            #   (c) backend unreachable / no conclusive answer → UNKNOWN: keep
            #       the occurrence in 'investigacao', do NOT advance and do NOT
            #       mark failed (absence of a local record is not proof of no
            #       transfer).
            if client is None:
                storage.update_occurrence(
                    r["id"], status="investigacao",
                    erro="recuperação: sem registro local e sem cliente backend — resultado desconhecido",
                )
                recovered += 1
                continue
            try:
                st = client.order_status(ordem_id, s.get("network") if s else None)
            except Exception as e:
                # backend down/timeout: financial result is UNKNOWN.
                storage.update_occurrence(
                    r["id"], status="investigacao",
                    erro=f"recuperação: backend indisponível ({str(e)[:80]}) — resultado desconhecido",
                )
                recovered += 1
                continue
            bstatus = st.get("status")
            if bstatus in _MOVED_STATUS:
                occ_status = _occurrence_status(bstatus)
                storage.update_occurrence(
                    r["id"], status=occ_status, ordem_id=ordem_id,
                    resgate=st.get("resgate"),
                    erro=(st.get("erro") or st.get("msg")) if occ_status == "failed" else None,
                )
                _advance_schedule(s, r["due_ts"])
            elif bstatus in _NO_FUNDS_STATUS:
                # PROVEN no transfer (order expired/never funded/cancelled/
                # unknown to the backend). Safe to conclude: nothing to recover.
                storage.update_occurrence(
                    r["id"], status="failed", ordem_id=ordem_id,
                    erro=f"ordem {bstatus} sem depósito — transferência não ocorreu",
                )
                _advance_schedule(s, r["due_ts"])
            else:
                # unrecognized/empty status → no conclusive answer.
                storage.update_occurrence(
                    r["id"], status="investigacao",
                    erro=f"recuperação: status backend não conclusivo ({bstatus!r}) — aguardando confirmação",
                )
        recovered += 1
    return recovered


def reconcile_pending_occurrences(client: BackendClient | None = None) -> int:
    """Close ``pending`` / ``investigacao`` occurrences (funds sent or unknown
    result, PIX not yet confirmed) once the backend reaches a definitive result
    — done/refunded/failed with resgate/receipt — and notify the agent.

    Also re-checks ``investigacao`` occurrences (whose schedule was deliberately
    NOT advanced during recovery) so the original order is resolved and the
    schedule advances as soon as the backend becomes reachable / conclusive
    again. A definitive answer never re-sends funds; an unreachable backend is
    retried on the next cycle.
    """
    storage.init()
    client = client or BackendClient()
    reconciled = 0
    with storage.conn() as c:
        rows = c.execute(
            "SELECT * FROM occurrences "
            "WHERE status IN ('pending','investigacao') AND ordem_id IS NOT NULL"
        ).fetchall()
    for r in rows:
        was_investigacao = r["status"] == "investigacao"
        tx_hash = r["tx_hash"]
        if not tx_hash:
            p = storage.get_payment(r["ordem_id"])
            if p:
                tx_hash = p.get("tx_hash")

        # 1) Resolve by the backend (order id). An unreachable backend is not a
        #    failure — fall through to the blockchain check by hash.
        bstatus = None
        st: dict = {}
        try:
            st = client.order_status(r["ordem_id"], None)
            bstatus = st.get("status")
        except Exception:
            bstatus = None

        if bstatus in _DONE_STATUS:
            storage.update_occurrence(r["id"], status="done", resgate=st.get("resgate"),
                                      erro=None)
            storage.enqueue_notification("agent", {
                "kind": "scheduled_resolved", "schedule_id": r["schedule_id"],
                "ordem_id": r["ordem_id"], "status": bstatus,
                "resgate": st.get("resgate"), "ts": int(time.time()),
            })
            if was_investigacao:
                _advance_schedule(storage.get_schedule(r["schedule_id"]), r["due_ts"])
            reconciled += 1
            continue
        if bstatus in _REFUNDED_STATUS:
            # A confirmed refund is NOT a successful PIX. Record it as its own
            # 'refunded' state and never emit a "Pix pago" message.
            storage.update_occurrence(r["id"], status="refunded",
                                      erro=f"estorno confirmado ({bstatus})")
            storage.enqueue_notification("agent", {
                "kind": "scheduled_resolved", "schedule_id": r["schedule_id"],
                "ordem_id": r["ordem_id"], "status": "refunded",
                "resgate": st.get("resgate"), "ts": int(time.time()),
            })
            if was_investigacao:
                _advance_schedule(storage.get_schedule(r["schedule_id"]), r["due_ts"])
            reconciled += 1
            continue
        if bstatus in _FAILED_STATUS:
            erro = st.get("erro") or st.get("msg")
            storage.update_occurrence(r["id"], status="failed", erro=erro)
            storage.enqueue_notification("agent", {
                "kind": "scheduled_resolved", "schedule_id": r["schedule_id"],
                "ordem_id": r["ordem_id"], "status": "failed",
                "erro": erro, "ts": int(time.time()),
            })
            if was_investigacao:
                _advance_schedule(storage.get_schedule(r["schedule_id"]), r["due_ts"])
            reconciled += 1
            continue
        if bstatus in _NO_FUNDS_STATUS:
            # PROVEN no transfer (order expired/cancelled without deposit).
            erro = f"ordem {bstatus} sem depósito — transferência não ocorreu"
            storage.update_occurrence(r["id"], status="failed", erro=erro)
            storage.enqueue_notification("agent", {
                "kind": "scheduled_resolved", "schedule_id": r["schedule_id"],
                "ordem_id": r["ordem_id"], "status": "failed",
                "erro": erro, "ts": int(time.time()),
            })
            if was_investigacao:
                _advance_schedule(storage.get_schedule(r["schedule_id"]), r["due_ts"])
            reconciled += 1
            continue

        # 2) Backend inconclusive (processing/pending/funding/notfound/None) —
        #    consult the blockchain by the persisted tx hash.
        if not tx_hash:
            continue   # nothing conclusive — retry next cycle
        state = _tx_state(tx_hash)
        if state == "mined":
            # Funds definitively on-chain → mark pending (PIX reconciliation
            # continues via the backend) and advance the schedule.
            if was_investigacao:
                storage.update_occurrence(r["id"], status="pending")
                _advance_schedule(storage.get_schedule(r["schedule_id"]), r["due_ts"])
                reconciled += 1
            continue
        if state == "notfound" and was_investigacao:
            # The signed tx is not on-chain and not in the mempool → the
            # broadcast definitively never landed → proven no transfer.
            erro = "transação não encontrada on-chain — transferência não ocorreu"
            storage.update_occurrence(r["id"], status="failed", erro=erro)
            storage.enqueue_notification("agent", {
                "kind": "scheduled_resolved", "schedule_id": r["schedule_id"],
                "ordem_id": r["ordem_id"], "status": "failed",
                "erro": erro, "ts": int(time.time()),
            })
            _advance_schedule(storage.get_schedule(r["schedule_id"]), r["due_ts"])
            reconciled += 1
            continue
        # 'pending' (mempool) / 'unknown' (RPC) → leave as-is, re-check next cycle.
    return reconciled


def run_due(client: BackendClient | None = None) -> int:
    """Process due schedules exactly once (idempotent). Returns executions."""
    storage.init()
    client = client or BackendClient()
    now = int(time.time())
    recover_stale_running(now, client)
    executed = 0
    for s in storage.due_schedules(now):
        sched_id = s["id"]
        # idempotency: skip if ANY occurrence exists for this due time
        # (pending/running/failed/done). The unique index on
        # (schedule_id, due_ts) makes the insert atomic across processes too,
        # so a schedule can never fire twice for the same due time.
        with storage.conn() as c:
            exists = c.execute(
                "SELECT COUNT(*) AS n FROM occurrences WHERE schedule_id=? AND due_ts=?",
                (sched_id, s["proxima_ocorrencia"]),
            ).fetchone()["n"]
        if exists:
            continue
        occ_id = storage.add_occurrence(sched_id, s["proxima_ocorrencia"])
        if occ_id is None:   # raced with another run — already scheduled
            continue
        storage.update_occurrence(occ_id, status="running", executed_ts=now)

        # Persist the occurrence→order association the moment the order is
        # created and BEFORE funds move (crash-safe recovery relies on it).
        def _link_ordem(ordem: str) -> None:
            storage.link_occurrence_payment(occ_id, ordem_id=ordem)

        res: dict = {"status": "failed"}
        occ_status = "failed"
        try:
            res = execute_schedule(s, client, on_order_created=_link_ordem)
            occ_status = _occurrence_status(res.get("status"))
            storage.update_occurrence(
                occ_id, status=occ_status, ordem_id=res.get("ordem"),
                tx_hash=res.get("tx_hash"),
                resgate=res.get("resgate") if occ_status == "done" else None,
                erro=res.get("erro") if occ_status in ("failed", "refunded", "investigacao") else None,
            )
        except Exception as e:
            # An exception escaping pay_pix is a PROVEN failure (e.g. insufficient
            # balance, sign failure before broadcast) — safe to conclude failed.
            storage.update_occurrence(occ_id, status="failed", erro=str(e)[:300])
            res = {"status": "failed", "erro": str(e)[:300]}
        # Advance the schedule ONLY on a definitive outcome. An uncertain
        # broadcast ('sending' → 'investigacao') must NOT advance: the schedule
        # is settled later once the blockchain/backend confirms the result.
        if occ_status != "investigacao":
            _advance_schedule(s, s["proxima_ocorrencia"])
        # notify result
        storage.enqueue_notification("agent", {
            "kind": "scheduled_result",
            "schedule_id": sched_id,
            "nome": s["nome"],
            "valor_brl": s["valor_brl"],
            "status": res.get("status"),
            "ordem": res.get("ordem"),
            "ts": int(time.time()),
        })
        executed += 1
    return executed


def run_loop(interval: int = 30) -> None:
    client = BackendClient()
    while True:
        try:
            run_due(client)
            reconcile_pending_occurrences(client)
        except Exception as e:
            print(f"[scheduler] erro: {str(e)[:120]}", flush=True)
        time.sleep(interval)
