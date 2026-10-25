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


def execute_schedule(schedule: dict, client: BackendClient) -> dict:
    """Execute one due schedule occurrence (respects reminder_only)."""
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
    )
    return {"status": result.get("status"), "ordem": result.get("ordem")}


def run_due(client: BackendClient | None = None) -> int:
    """Process due schedules exactly once (idempotent). Returns executions."""
    storage.init()
    client = client or BackendClient()
    now = int(time.time())
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
        res: dict = {"status": "failed"}
        try:
            res = execute_schedule(s, client)
            storage.update_occurrence(occ_id, status="done", ordem_id=res.get("ordem"),
                                      erro=None)
        except Exception as e:
            storage.update_occurrence(occ_id, status="failed", erro=str(e)[:300])
            res = {"status": "failed", "erro": str(e)[:300]}
        # advance schedule
        nxt = next_occurrence(s, s["proxima_ocorrencia"])
        reps = int(s.get("reps_executadas") or 0) + 1
        if nxt is None:
            storage.update_schedule(sched_id, status="done", reps_executadas=reps)
        else:
            storage.update_schedule(sched_id, proxima_ocorrencia=nxt, reps_executadas=reps)
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
        except Exception as e:
            print(f"[scheduler] erro: {str(e)[:120]}", flush=True)
        time.sleep(interval)
