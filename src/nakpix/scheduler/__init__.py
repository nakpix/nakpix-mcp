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
            "resgate": result.get("resgate")}


_DONE_STATUS = ("sent", "done", "solucionado", "resolved")
_REFUNDED_STATUS = ("refunded", "extornado")
_FAILED_STATUS = ("erro", "err", "failed", "notfound")


def _occurrence_status(result_status: str | None) -> str:
    """Map a pay_pix result status to an occurrence status.

    A 'pending' PIX (funds sent, still processing) must NOT be recorded as
    'done' — it is tracked to completion by the incident monitor. A refund
    ('refunded'/'extornado') is recorded as its own 'refunded' state — it is
    NOT a successful PIX and must never read as one.
    """
    if result_status in _DONE_STATUS:
        return "done"
    if result_status in _REFUNDED_STATUS:
        return "refunded"
    if result_status in _FAILED_STATUS:
        return "failed"
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
    - ``ordem_id`` present + no local payment → the transmission happened but
      the result was never recorded: ask the backend for the real order status
      and reconcile from it (or mark failed when no deposit ever arrived).
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
            _advance_schedule(s, r["due_ts"])
        elif payment and not payment.get("tx_hash"):
            storage.update_occurrence(
                r["id"], status="failed", ordem_id=ordem_id,
                erro=payment.get("erro") or "financiamento não transmitido",
            )
            _advance_schedule(s, r["due_ts"])
        else:
            # Transmission happened but the result was never recorded: consult
            # the backend by the EXPLICIT order id (never by key+value).
            bstatus = None
            resgate = None
            erro = None
            if client is not None:
                try:
                    st = client.order_status(ordem_id, s.get("network") if s else None)
                    bstatus = st.get("status")
                    resgate = st.get("resgate")
                    erro = st.get("erro") or st.get("msg")
                except Exception as e:
                    erro = str(e)[:120]
            if bstatus in ("funding", "sent", "done", "solucionado", "resolved",
                           "refunded", "extornado", "pending", "processing"):
                occ_status = _occurrence_status(bstatus)
                storage.update_occurrence(
                    r["id"], status=occ_status, ordem_id=ordem_id,
                    resgate=resgate,
                    erro=erro if occ_status == "failed" else None,
                )
                _advance_schedule(s, r["due_ts"])
            else:
                storage.update_occurrence(
                    r["id"], status="failed", ordem_id=ordem_id,
                    erro="ordem criada sem depósito confirmado (sem transferência)",
                )
                _advance_schedule(s, r["due_ts"])
        recovered += 1
    return recovered


def reconcile_pending_occurrences(client: BackendClient | None = None) -> int:
    """Update ``pending`` occurrences (funds sent, PIX not yet confirmed) once
    the backend reaches a definitive result — done/failed with resgate/receipt —
    and notify the agent of the resolution."""
    storage.init()
    client = client or BackendClient()
    reconciled = 0
    with storage.conn() as c:
        rows = c.execute(
            "SELECT * FROM occurrences WHERE status='pending' AND ordem_id IS NOT NULL"
        ).fetchall()
    for r in rows:
        try:
            st = client.order_status(r["ordem_id"], None)
        except Exception:
            continue   # backend unreachable — retry next cycle
        bstatus = st.get("status")
        if bstatus in _DONE_STATUS:
            storage.update_occurrence(r["id"], status="done", resgate=st.get("resgate"),
                                      erro=None)
            storage.enqueue_notification("agent", {
                "kind": "scheduled_resolved", "schedule_id": r["schedule_id"],
                "ordem_id": r["ordem_id"], "status": bstatus,
                "resgate": st.get("resgate"), "ts": int(time.time()),
            })
            reconciled += 1
        elif bstatus in _REFUNDED_STATUS:
            # A confirmed refund is NOT a successful PIX. Record it as its own
            # 'refunded' state and never emit a "Pix pago" message.
            storage.update_occurrence(r["id"], status="refunded",
                                      erro=f"estorno confirmado ({bstatus})")
            storage.enqueue_notification("agent", {
                "kind": "scheduled_resolved", "schedule_id": r["schedule_id"],
                "ordem_id": r["ordem_id"], "status": "refunded",
                "resgate": st.get("resgate"), "ts": int(time.time()),
            })
            reconciled += 1
        elif bstatus in _FAILED_STATUS:
            storage.update_occurrence(r["id"], status="failed",
                                      erro=st.get("erro") or st.get("msg"))
            storage.enqueue_notification("agent", {
                "kind": "scheduled_resolved", "schedule_id": r["schedule_id"],
                "ordem_id": r["ordem_id"], "status": "failed",
                "erro": st.get("erro") or st.get("msg"), "ts": int(time.time()),
            })
            reconciled += 1
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
        try:
            res = execute_schedule(s, client, on_order_created=_link_ordem)
            occ_status = _occurrence_status(res.get("status"))
            storage.update_occurrence(
                occ_id, status=occ_status, ordem_id=res.get("ordem"),
                resgate=res.get("resgate") if occ_status == "done" else None,
                erro=res.get("erro") if occ_status in ("failed", "refunded") else None,
            )
        except Exception as e:
            storage.update_occurrence(occ_id, status="failed", erro=str(e)[:300])
            res = {"status": "failed", "erro": str(e)[:300]}
        # advance schedule
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
