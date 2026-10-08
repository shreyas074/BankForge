"""
Shared test bootstrap — every test module imports this first. Module-level
code runs exactly once per test process (Python caches imports), so all
three isolated databases get force-reseeded exactly once per test run,
regardless of how many test files import this module or in what order.
"""
from __future__ import annotations

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from accounts_server import db as accounts_db
from products_server import db as products_db
from compliance_comms_server import db as compliance_db

accounts_db.init_db(force=True)
products_db.init_db(force=True)
compliance_db.init_db(force=True)
