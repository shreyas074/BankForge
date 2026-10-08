"""
compliance_comms_server — isolated data layer.

Owns its own SQLite file (neobank_compliance.db) with the authoritative
customer/KYC record plus fraud_flags, audit_log, and communications_log.
This is the only module anywhere in the project permitted to query a full
customer record (email/phone/segment/KYC) — accounts_server keeps only a
minimal copy (name/kyc_status/risk_rating) for its own field-visibility
scope, never the full record.

No table here is reachable from accounts_server's or products_server's own
db.py files — they are separate SQLite files on disk, not separate
connections to one shared file. See tests/test_server_isolation.py.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from logging_config import get_logger, trace

logger = get_logger(__name__)

DB_PATH = Path(os.environ.get(
    "COMPLIANCE_DB_PATH",
    str(Path(__file__).resolve().parent.parent / "data" / "neobank_compliance.db"),
))

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    customer_id TEXT PRIMARY KEY,   -- CUS-XXXXX
    name TEXT,
    email TEXT,
    phone TEXT,
    kyc_status TEXT,                -- verified | pending | rejected
    risk_rating TEXT,                -- low | medium | high
    segment TEXT,                    -- retail | small_business
    account_tier TEXT                -- standard | premium | elite
);

CREATE TABLE IF NOT EXISTS fraud_flags (
    flag_id TEXT PRIMARY KEY,        -- FLAG-XXXXX
    customer_id TEXT,
    flag_type TEXT,                  -- velocity | geo_mismatch | device_change | manual_review
    severity TEXT,                   -- low | medium | high
    created_at TEXT,
    status TEXT,                     -- open | cleared
    FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    entry_id TEXT PRIMARY KEY,       -- AUD-XXXXXX
    timestamp TEXT,
    action_type TEXT,
    performed_by TEXT,
    customer_id TEXT,
    outcome TEXT,
    details TEXT
);

CREATE TABLE IF NOT EXISTS communications_log (
    comm_id TEXT PRIMARY KEY,        -- COMM-XXXXX
    customer_id TEXT,
    channel TEXT,
    message_preview TEXT,            -- first 50 chars only -- minimization at write time
    status TEXT,
    sent_at TEXT
);
"""

CUSTOMERS = [
    ("CUS-10001", "Ananya Rao", "ananya.rao@example.com", "9800000001", "verified", "low", "retail", "standard"),
    ("CUS-10002", "Vikram Shah", "vikram.shah@example.com", "9800000002", "verified", "low", "small_business", "premium"),
    ("CUS-10003", "Priya Nair", "priya.nair@example.com", "9800000003", "pending", "medium", "retail", "standard"),
    ("CUS-10004", "Rohan Mehta", "rohan.mehta@example.com", "9800000004", "verified", "high", "retail", "standard"),
    ("CUS-10005", "Sneha Iyer", "sneha.iyer@example.com", "9800000005", "verified", "low", "small_business", "premium"),
    ("CUS-10006", "Arjun Kapoor", "arjun.kapoor@example.com", "9800000006", "rejected", "high", "retail", "standard"),
    # Official Showcase Scenario customer:
    ("CUS-10042", "Priya Sharma", "priya.sharma@example.com", "9800010042", "verified", "low", "retail", "elite"),
]

FRAUD_FLAGS = [
    ("FLAG-10001", "CUS-10004", "velocity", "medium", "2026-08-20", "open"),
    ("FLAG-10002", "CUS-10006", "manual_review", "high", "2026-07-15", "open"),
    ("FLAG-10003", "CUS-10002", "geo_mismatch", "low", "2026-06-01", "cleared"),
]


@trace(logger)
def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


@trace(logger)
def init_db(force: bool = False) -> None:
    if force and DB_PATH.exists():
        DB_PATH.unlink()
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = _connect()
    conn.executescript(SCHEMA)
    conn.executemany("INSERT OR IGNORE INTO customers VALUES (?,?,?,?,?,?,?,?)", CUSTOMERS)
    conn.executemany("INSERT OR IGNORE INTO fraud_flags VALUES (?,?,?,?,?,?)", FRAUD_FLAGS)
    conn.commit()
    conn.close()


@trace(logger)
def get_customer(customer_id: str) -> dict | None:
    conn = _connect()
    row = conn.execute("SELECT * FROM customers WHERE customer_id = ?", (customer_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


@trace(logger)
def get_fraud_flags(customer_id: str) -> list[dict]:
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM fraud_flags WHERE customer_id = ? ORDER BY created_at DESC", (customer_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@trace(logger)
def insert_audit_log(entry_id: str, timestamp: str, action_type: str, performed_by: str,
                      customer_id: str, outcome: str, details: str) -> None:
    conn = _connect()
    conn.execute(
        "INSERT INTO audit_log VALUES (?,?,?,?,?,?,?)",
        (entry_id, timestamp, action_type, performed_by, customer_id, outcome, details),
    )
    conn.commit()
    conn.close()


@trace(logger)
def get_audit_log_for_customer(customer_id: str) -> list[dict]:
    """Supports the 'retrievable for compliance review' part of §4's audit
    logging requirement."""
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM audit_log WHERE customer_id = ? ORDER BY timestamp DESC", (customer_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@trace(logger)
def insert_communication_log(comm_id: str, customer_id: str, channel: str, message: str,
                              status: str, sent_at: str) -> None:
    conn = _connect()
    conn.execute(
        "INSERT INTO communications_log VALUES (?,?,?,?,?,?)",
        (comm_id, customer_id, channel, message[:50], status, sent_at),
    )
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db(force=True)
    print(f"Seeded {DB_PATH}")
    print(f"Customers: {len(CUSTOMERS)} | Fraud flags: {len(FRAUD_FLAGS)}")
