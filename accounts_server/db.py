"""
accounts_server — isolated data layer.

This module owns its own SQLite file (neobank_accounts.db) and exposes
ONLY the two tables accounts_server is responsible for: accounts and a
minimal customers copy (name/kyc_status/risk_rating — just enough to
support the compliance_officer field-visibility scope; the authoritative,
full customer record lives in compliance_comms_server).

Isolation is structural, not incidental: there is no function in this
file, and no table in this file's schema, that could return loan_products,
fraud_flags, audit_log, or communications_log data — those tables simply
do not exist here. See tests/test_server_isolation.py, which asserts this
by inspecting the schema and source directly.
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
    "ACCOUNTS_DB_PATH",
    str(Path(__file__).resolve().parent.parent / "data" / "neobank_accounts.db"),
))

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    customer_id TEXT PRIMARY KEY,   -- CUS-XXXXX
    name TEXT,
    kyc_status TEXT,                -- verified | pending | rejected
    risk_rating TEXT                -- low | medium | high
);

CREATE TABLE IF NOT EXISTS accounts (
    account_id TEXT PRIMARY KEY,    -- ACC-XXXXX
    customer_id TEXT,
    account_number TEXT,            -- long customer-facing number; masked before leaving the server
    account_type TEXT,              -- savings | current | business
    balance REAL,
    status TEXT,                    -- active | dormant | frozen
    FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
);

CREATE TABLE IF NOT EXISTS transactions (
    transaction_id TEXT PRIMARY KEY,  -- TXN-XXXXX
    account_id TEXT,
    amount REAL,
    direction TEXT,                   -- credit | debit
    description TEXT,
    txn_date TEXT,
    FOREIGN KEY (account_id) REFERENCES accounts(account_id)
);
"""

CUSTOMERS = [
    ("CUS-10001", "Ananya Rao", "verified", "low"),
    ("CUS-10002", "Vikram Shah", "verified", "low"),
    ("CUS-10003", "Priya Nair", "pending", "medium"),
    ("CUS-10004", "Rohan Mehta", "verified", "high"),
    ("CUS-10005", "Sneha Iyer", "verified", "low"),
    ("CUS-10006", "Arjun Kapoor", "rejected", "high"),
    # Official Showcase Scenario customer:
    ("CUS-10042", "Priya Sharma", "verified", "low"),
]

ACCOUNTS = [
    ("ACC-10001", "CUS-10001", "400812340001", "savings", 85000.0, "active"),
    ("ACC-10002", "CUS-10002", "400812340002", "business", 1250000.0, "active"),
    ("ACC-10003", "CUS-10003", "400812340003", "savings", 12000.0, "active"),
    ("ACC-10004", "CUS-10004", "400812340004", "current", 500.0, "dormant"),
    ("ACC-10005", "CUS-10005", "400812340005", "business", 340000.0, "active"),
    ("ACC-10006", "CUS-10006", "400812340006", "savings", 0.0, "frozen"),
    # Official Showcase Scenario account:
    ("ACC-10042", "CUS-10042", "400812349042", "savings", 215000.0, "active"),
]

TRANSACTIONS = [
    ("TXN-00001", "ACC-10001", 15000.0, "credit", "Salary credit", "2026-09-01"),
    ("TXN-00002", "ACC-10001", 2000.0, "debit", "ATM withdrawal", "2026-09-03"),
    ("TXN-00003", "ACC-10002", 1050000.0, "credit", "Client payment received", "2026-09-05"),
    ("TXN-00004", "ACC-10002", 300000.0, "debit", "Supplier payment", "2026-09-06"),
    ("TXN-00005", "ACC-10003", 500.0, "debit", "UPI payment", "2026-09-07"),
    ("TXN-00006", "ACC-10005", 75000.0, "credit", "Invoice settlement", "2026-09-08"),
    ("TXN-00007", "ACC-10042", 18000.0, "credit", "Salary credit", "2026-09-10"),
    ("TXN-00008", "ACC-10042", 1200.0, "debit", "Grocery UPI payment", "2026-09-12"),
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
    conn.executemany("INSERT OR IGNORE INTO customers VALUES (?,?,?,?)", CUSTOMERS)
    conn.executemany("INSERT OR IGNORE INTO accounts VALUES (?,?,?,?,?,?)", ACCOUNTS)
    conn.executemany("INSERT OR IGNORE INTO transactions VALUES (?,?,?,?,?,?)", TRANSACTIONS)
    conn.commit()
    conn.close()


@trace(logger)
def get_account(account_id: str) -> dict | None:
    conn = _connect()
    row = conn.execute("SELECT * FROM accounts WHERE account_id = ?", (account_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


@trace(logger)
def get_accounts_for_customer(customer_id: str) -> list[dict]:
    conn = _connect()
    rows = conn.execute("SELECT * FROM accounts WHERE customer_id = ?", (customer_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@trace(logger)
def get_transactions(account_id: str, limit: int = 10) -> list[dict]:
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM transactions WHERE account_id = ? ORDER BY txn_date DESC LIMIT ?",
        (account_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@trace(logger)
def get_customer_minimal(customer_id: str) -> dict | None:
    """Only ever used internally to populate kyc_status/risk_rating for the
    compliance_officer field-visibility scope — see guardrails.py."""
    conn = _connect()
    row = conn.execute("SELECT * FROM customers WHERE customer_id = ?", (customer_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


if __name__ == "__main__":
    init_db(force=True)
    print(f"Seeded {DB_PATH}")
    print(f"Customers: {len(CUSTOMERS)} | Accounts: {len(ACCOUNTS)} | Transactions: {len(TRANSACTIONS)}")
