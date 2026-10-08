"""
products_server — FastMCP registration & entry point.

    pip install mcp
    python -m products_server              # stdio transport
    mcp dev products_server/__main__.py    # MCP Inspector

Runs as its own standalone local process — no Docker.
"""
from __future__ import annotations

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import guardrails
from products_server import db, logic
from logging_config import get_logger, trace

from mcp.server.fastmcp import FastMCP  # pip install mcp

logger = get_logger("products_server.server")

mcp = FastMCP("neobank-products-server")

# Every @mcp.tool()/@mcp.resource() function below is also @trace(logger)'d
# directly, in addition to the logic.py function it delegates to — see
# accounts_server/__main__.py's module docstring for why both layers are
# traced rather than just one.


@mcp.tool()
@trace(logger)
def list_loan_products(category: str | None = None) -> dict:
    """Catalogue browse, optional category filter (personal | home | business | vehicle)."""
    return logic.list_loan_products(category)


@mcp.tool()
@trace(logger)
def get_loan_product_details(product_id: str) -> dict:
    """Single product lookup."""
    return logic.get_loan_product_details(product_id)


@mcp.tool()
@trace(logger)
def check_eligibility_criteria(product_id: str, applicant_risk_rating: str) -> dict:
    """Criteria check only — no write, no customer data touches this server."""
    return logic.check_eligibility_criteria(product_id, applicant_risk_rating)


@mcp.tool()
@trace(logger, redact=guardrails.redact_for_logging)
def submit_loan_application(customer_id: str, product_id: str, requested_amount: float) -> dict:
    """Submit a loan application. Enforces the KYC gate at the Tool level —
    independently verified, not left to the AI client's say-so."""
    return logic.submit_loan_application(customer_id, product_id, requested_amount)


@mcp.tool()
@trace(logger, redact=guardrails.redact_for_logging)
def get_loan_application_status(application_id: str) -> dict:
    """Read operation over stored loan application records."""
    return logic.get_loan_application_status(application_id)


@mcp.resource("product://{product_id}/details")
@trace(logger)
def product_details(product_id: str) -> dict:
    """A single loan product's details."""
    return logic.product_details_resource(product_id)


if __name__ == "__main__":
    db.init_db(force=False)
    logger.info("Starting products_server (stdio transport)")
    mcp.run(transport="stdio")
