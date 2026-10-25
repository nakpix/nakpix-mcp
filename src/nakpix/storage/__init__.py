"""SQLite storage — scheduler, payment history, incidents, notifications.

Separate from the wallet files (which are encrypted JSON). No secrets here:
transaction hashes and order ids are operational data, never keys/seeds.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager

from ..config import ensure_dirs, NAKPIX_HOME

DB_PATH = NAKPIX_HOME / "nakpix.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    updated_at INTEGER,
    ordem_id TEXT UNIQUE,
    pix_brl REAL NOT NULL,
    chave TEXT,
    network TEXT,
    token TEXT,
    usdt REAL,
    fee_pct REAL,
    cupom TEXT,
    status TEXT DEFAULT 'created',
    tx_hash TEXT,
    resgate TEXT,
    erro TEXT
);
CREATE INDEX IF NOT EXISTS idx_payments_ts ON payments(ts);

CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_created INTEGER NOT NULL,
    nome TEXT NOT NULL,
    chave TEXT NOT NULL,
    valor_brl REAL NOT NULL,
    network TEXT NOT NULL DEFAULT 'polygon',
    cupom TEXT,
    recurrencia TEXT DEFAULT 'once',      -- once|daily|weekly|monthly|yearly|interval
    intervalo_dias INTEGER,               -- for interval
    proxima_ocorrencia INTEGER NOT NULL,
    data_fim INTEGER,
    max_reps INTEGER,
    reps_executadas INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active',          -- active|paused|cancelled|done
    reminder_only INTEGER DEFAULT 0,
    dia_ancora INTEGER
);
CREATE INDEX IF NOT EXISTS idx_schedules_due ON schedules(status, proxima_ocorrencia);

CREATE TABLE IF NOT EXISTS occurrences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    schedule_id INTEGER NOT NULL,
    due_ts INTEGER NOT NULL,
    executed_ts INTEGER,
    status TEXT DEFAULT 'pending',         -- pending|running|done|failed
    ordem_id TEXT,
    erro TEXT
);
CREATE INDEX IF NOT EXISTS idx_occ_due ON occurrences(status, due_ts);

CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    dedup_key TEXT UNIQUE,
    ordem_id TEXT,
    tipo TEXT DEFAULT 'unresolved_pix',
    status TEXT DEFAULT 'open',            -- open|resolved
    email_sent INTEGER DEFAULT 0,
    email_ts INTEGER,
    resolvido_ts INTEGER
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    channel TEXT DEFAULT 'agent',
    payload TEXT,
    delivered INTEGER DEFAULT 0
);
"""


@contextmanager
def conn():
    ensure_dirs()
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init() -> None:
    with conn() as c:
        c.executescript(_SCHEMA)
        _migrate(c)


def _add_column(c: sqlite3.Connection, table: str, column: str, type_: str) -> None:
    cols = [r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {type_}")


def _migrate(c: sqlite3.Connection) -> None:
    """Idempotent, NON-DESTRUCTIVE migrations. Safe to run on every init().

    Never DELETEs financial records. Done/failed occurrences (and their
    ordem_id/tx) are always preserved.
    """
    # occurrences: drop the legacy FULL unique index (from an earlier build
    # that also deleted duplicates) and replace it with a PARTIAL one that only
    # blocks two ACTIVE (pending/running) rows for the same (schedule_id,
    # due_ts). Historical done/failed rows are untouched.
    c.execute("DROP INDEX IF EXISTS idx_occ_unique")
    # Legacy duplicate ACTIVE rows carry no financial id (ordem_id is set only
    # on 'done'); demote the later ones to 'failed' (preserved, no longer
    # active) so the partial index can be created.
    c.execute(
        "UPDATE occurrences SET status='failed' "
        "WHERE status IN ('pending','running') AND id NOT IN ("
        "  SELECT MIN(id) FROM occurrences WHERE status IN ('pending','running') "
        "  GROUP BY schedule_id, due_ts)"
    )
    c.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_occ_active "
        "ON occurrences(schedule_id, due_ts) WHERE status IN ('pending','running')"
    )

    # payments: updated_at (last write) alongside the immutable ts anchor.
    _add_column(c, "payments", "updated_at", "INTEGER")

    # schedules: dia_ancora keeps the original day-of-month for monthly
    # recurrence so "day 31" clamps correctly across shorter months.
    _add_column(c, "schedules", "dia_ancora", "INTEGER")


def _now() -> int:
    return int(time.time())


# ── payments ───────────────────────────────────────────────────────────────
def record_payment(*, ordem_id: str | None = None, pix_brl: float, network: str,
                   token: str | None = None, usdt: float | None = None,
                   fee_pct: float | None = None, cupom: str | None = None,
                   chave: str | None = None, status: str = "created",
                   tx_hash: str | None = None, resgate: str | None = None,
                   erro: str | None = None) -> int:
    """Upsert a payment row keyed by ``ordem_id``.

    - ``ts`` is the immutable FINANCIAL anchor (first write = funds sent) used
      by the 15-min monitor; it is never overwritten by later updates.
    - ``pix_brl`` is also immutable (the BRL amount of an order never changes);
      a later update passing 0 does NOT overwrite the real value.
    - ``updated_at`` tracks the last write.
    - Optional fields merge with COALESCE so a partial update never wipes
      already-stored values (chave, valor BRL, cupom, resgate). ``status`` is
      always set to the latest value passed.
    """
    init()
    with conn() as c:
        if ordem_id:
            c.execute(
                "INSERT INTO payments (ts, updated_at, ordem_id, pix_brl, chave, network, "
                "token, usdt, fee_pct, cupom, status, tx_hash, resgate, erro) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(ordem_id) DO UPDATE SET "
                "updated_at=excluded.updated_at, "
                "chave=COALESCE(excluded.chave, payments.chave), "
                "network=COALESCE(excluded.network, payments.network), "
                "token=COALESCE(excluded.token, payments.token), "
                "usdt=COALESCE(excluded.usdt, payments.usdt), "
                "fee_pct=COALESCE(excluded.fee_pct, payments.fee_pct), "
                "cupom=COALESCE(excluded.cupom, payments.cupom), "
                "status=excluded.status, "
                "tx_hash=COALESCE(excluded.tx_hash, payments.tx_hash), "
                "resgate=COALESCE(excluded.resgate, payments.resgate), "
                "erro=COALESCE(excluded.erro, payments.erro)",
                (_now(), _now(), ordem_id, pix_brl, chave, network, token, usdt, fee_pct,
                 cupom, status, tx_hash, resgate, erro),
            )
            row = c.execute("SELECT id FROM payments WHERE ordem_id=?",
                            (ordem_id,)).fetchone()
            return row["id"] if row else 0
        cur = c.execute(
            "INSERT INTO payments (ts, updated_at, pix_brl, chave, network, token, usdt, "
            "fee_pct, cupom, status, tx_hash, resgate, erro) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (_now(), _now(), pix_brl, chave, network, token, usdt, fee_pct, cupom,
             status, tx_hash, resgate, erro),
        )
        return cur.lastrowid


def list_payments(limit: int = 50) -> list[dict]:
    init()
    with conn() as c:
        rows = c.execute("SELECT * FROM payments ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def list_unresolved_payments(cutoff: int, limit: int = 500, offset: int = 0) -> list[dict]:
    """Payments with an on-chain tx_hash but no terminal status, older than
    ``cutoff`` (oldest first). The incident monitor pages through ALL matches
    via ``offset`` so no unresolved operation is ever missed by a cap."""
    init()
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM payments "
            "WHERE tx_hash IS NOT NULL AND tx_hash != '' "
            "AND status NOT IN ('sent','done','solucionado','refunded','extornado','resolved') "
            "AND ts <= ? ORDER BY ts ASC LIMIT ? OFFSET ?",
            (cutoff, limit, offset),
        ).fetchall()
    return [dict(r) for r in rows]


def get_payment(ordem_id: str) -> dict | None:
    init()
    with conn() as c:
        r = c.execute("SELECT * FROM payments WHERE ordem_id=?", (ordem_id,)).fetchone()
    return dict(r) if r else None


# ── schedules ──────────────────────────────────────────────────────────────
def add_schedule(*, nome: str, chave: str, valor_brl: float, network: str,
                 cupom: str | None = None, recurrencia: str = "once",
                 intervalo_dias: int | None = None, proxima_ocorrencia: int,
                 data_fim: int | None = None, max_reps: int | None = None,
                 reminder_only: int = 0) -> int:
    init()
    dia_ancora = time.localtime(proxima_ocorrencia).tm_mday
    with conn() as c:
        cur = c.execute(
            "INSERT INTO schedules (ts_created, nome, chave, valor_brl, network, cupom, "
            "recurrencia, intervalo_dias, proxima_ocorrencia, data_fim, max_reps, "
            "reps_executadas, status, reminder_only, dia_ancora) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,0,'active',?,?)",
            (_now(), nome, chave, valor_brl, network, cupom, recurrencia, intervalo_dias,
             proxima_ocorrencia, data_fim, max_reps, reminder_only, dia_ancora),
        )
        return cur.lastrowid


def list_schedules() -> list[dict]:
    init()
    with conn() as c:
        rows = c.execute("SELECT * FROM schedules ORDER BY proxima_ocorrencia ASC").fetchall()
    return [dict(r) for r in rows]


def get_schedule(schedule_id: int) -> dict | None:
    init()
    with conn() as c:
        r = c.execute("SELECT * FROM schedules WHERE id=?", (schedule_id,)).fetchone()
    return dict(r) if r else None


def update_schedule(schedule_id: int, **fields) -> None:
    init()
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    with conn() as c:
        c.execute(f"UPDATE schedules SET {cols} WHERE id=?", (*fields.values(), schedule_id))


def due_schedules(now: int | None = None) -> list[dict]:
    init()
    now = now or _now()
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM schedules WHERE status='active' AND proxima_ocorrencia<=?",
            (now,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── occurrences ────────────────────────────────────────────────────────────
def add_occurrence(schedule_id: int, due_ts: int) -> int | None:
    """Insert an occurrence, or return None if (schedule_id, due_ts) already
    exists (unique index). Callers must treat None as 'already scheduled'."""
    init()
    with conn() as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO occurrences (schedule_id, due_ts, status) "
            "VALUES (?,?,'pending')",
            (schedule_id, due_ts),
        )
        return cur.lastrowid if cur.rowcount else None


def get_occurrence(occurrence_id: int) -> dict | None:
    init()
    with conn() as c:
        r = c.execute("SELECT * FROM occurrences WHERE id=?", (occurrence_id,)).fetchone()
    return dict(r) if r else None


def update_occurrence(occurrence_id: int, **fields) -> None:
    init()
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    with conn() as c:
        c.execute(f"UPDATE occurrences SET {cols} WHERE id=?", (*fields.values(), occurrence_id))


def due_occurrences(now: int | None = None) -> list[dict]:
    init()
    now = now or _now()
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM occurrences WHERE status='pending' AND due_ts<=? ORDER BY due_ts ASC",
            (now,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── incidents ──────────────────────────────────────────────────────────────
def create_incident(*, dedup_key: str, ordem_id: str | None = None,
                    tipo: str = "unresolved_pix") -> dict:
    init()
    with conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO incidents (ts, dedup_key, ordem_id, tipo, status) "
            "VALUES (?,?,?,?,'open')",
            (_now(), dedup_key, ordem_id, tipo),
        )
        r = c.execute("SELECT * FROM incidents WHERE dedup_key=?", (dedup_key,)).fetchone()
    return dict(r)


def get_open_incidents() -> list[dict]:
    init()
    with conn() as c:
        rows = c.execute("SELECT * FROM incidents WHERE status='open'").fetchall()
    return [dict(r) for r in rows]


def update_incident(incident_id: int, **fields) -> None:
    init()
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    with conn() as c:
        c.execute(f"UPDATE incidents SET {cols} WHERE id=?", (*fields.values(), incident_id))


# ── notifications ──────────────────────────────────────────────────────────
def enqueue_notification(channel: str, payload: dict) -> int:
    init()
    with conn() as c:
        cur = c.execute(
            "INSERT INTO notifications (ts, channel, payload, delivered) VALUES (?,?,?,0)",
            (_now(), channel, json.dumps(payload, ensure_ascii=False)),
        )
        return cur.lastrowid


def pending_notifications(channel: str | None = None) -> list[dict]:
    init()
    with conn() as c:
        if channel:
            rows = c.execute(
                "SELECT * FROM notifications WHERE delivered=0 AND channel=? ORDER BY ts ASC",
                (channel,),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM notifications WHERE delivered=0 ORDER BY ts ASC"
            ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["payload"] = json.loads(d["payload"])
        except Exception:
            pass
        out.append(d)
    return out


def mark_notification_delivered(notification_id: int) -> None:
    init()
    with conn() as c:
        c.execute("UPDATE notifications SET delivered=1 WHERE id=?", (notification_id,))
