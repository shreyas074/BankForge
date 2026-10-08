"""
products_server — isolated data layer.

Owns its own SQLite file (neobank_products.db) with ONLY loan_products and
loan_applications. There is no customers or accounts table in this
schema, no function in this file that could query one, and no import of
accounts_server.db or compliance_comms_server.db here — this is what makes
"products_server cannot see customer data" structural rather than
incidental. See tests/test_server_isolation.py.

Any check that genuinely needs customer/KYC data (the KYC gate inside
submit_loan_application) is performed by calling compliance_comms_server's
own tool-logic module directly — see products_server/logic.py — never by
this module reaching into another server's storage.
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
    "PRODUCTS_DB_PATH",
    str(Path(__file__).resolve().parent.parent / "data" / "neobank_products.db"),
))

SCHEMA = """
CREATE TABLE IF NOT EXISTS loan_products (
    product_id TEXT PRIMARY KEY,       -- PROD-XX-XX
    name TEXT,
    category TEXT,                     -- personal | home | business | vehicle
    interest_rate REAL,
    max_amount REAL,
    max_acceptable_risk_rating TEXT     -- low | medium | high -- highest applicant risk this product accepts
);

CREATE TABLE IF NOT EXISTS loan_applications (
    application_id TEXT PRIMARY KEY,   -- APP-XXXXX
    customer_id TEXT,                  -- stored only as an opaque reference string, never queried against
    product_id TEXT,
    requested_amount REAL,
    status TEXT,                       -- submitted | rejected
    submitted_at TEXT,
    decision_reason TEXT,
    FOREIGN KEY (product_id) REFERENCES loan_products(product_id)
);
"""

# Risk severity order, low to high — used only for the eligibility comparison.
RISK_ORDER = {"low": 0, "medium": 1, "high": 2}

LOAN_PRODUCTS = [
    ("PROD-PL-01", "NeoBank Personal Loan", "personal", 11.5, 500000.0, "medium"),
    ("PROD-HL-01", "NeoBank Home Loan", "home", 8.4, 7500000.0, "low"),
    ("PROD-BL-01", "NeoBank Business Growth Loan", "business", 12.0, 2000000.0, "medium"),
    ("PROD-VL-01", "NeoBank Vehicle Loan", "vehicle", 9.8, 1000000.0, "medium"),
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
    conn.executemany("INSERT OR IGNORE INTO loan_products VALUES (?,?,?,?,?,?)", LOAN_PRODUCTS)
    conn.commit()
    conn.close()


@trace(logger)
def list_products(category: str | None = None) -> list[dict]:
    conn = _connect()
    if category:
        rows = conn.execute("SELECT * FROM loan_products WHERE category = ?", (category,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM loan_products").fetchall()
    conn.close()
    return [dict(r) for r in rows]


@trace(logger)
def get_product(product_id: str) -> dict | None:
    conn = _connect()
    row = conn.execute("SELECT * FROM loan_products WHERE product_id = ?", (product_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


@trace(logger)
def insert_application(application_id: str, customer_id: str, product_id: str,
                        requested_amount: float, status: str, submitted_at: str,
                        decision_reason: str) -> None:
    conn = _connect()
    conn.execute(
        "INSERT INTO loan_applications VALUES (?,?,?,?,?,?,?)",
        (application_id, customer_id, product_id, requested_amount, status, submitted_at, decision_reason),
    )
    conn.commit()
    conn.close()


@trace(logger)
def get_application(application_id: str) -> dict | None:
    conn = _connect()
    row = conn.execute(
        "SELECT * FROM loan_applications WHERE application_id = ?", (application_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


if __name__ == "__main__":
    init_db(force=True)
    print(f"Seeded {DB_PATH}")
    print(f"Loan products: {len(LOAN_PRODUCTS)}")
