"""
accounts_server — FastMCP registration & entry point.

Turns logic.py's plain functions into live MCP Tools and a Resource.
Written against the documented `mcp` package API (mcp>=1.2.0) — verify it
against the real package on your machine before submission, per brief §6:

    pip install mcp
    python -m accounts_server              # stdio transport, for Claude Desktop
    mcp dev accounts_server/__main__.py    # MCP Inspector

Runs as its own standalone local process — no Docker, no container
runtime, per brief §2.
"""
from __future__ import annotations

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import guardrails
from accounts_server import db, logic
from logging_config import get_logger, trace

from mcp.server.fastmcp import FastMCP  # pip install mcp

logger = get_logger("accounts_server.server")

mcp = FastMCP("neobank-accounts-server")

# Every @mcp.tool()/@mcp.resource()/@mcp.prompt() function below is also
# @trace(logger)'d directly — not just the logic.py function it delegates
# to. §5's warning box is explicit that inconsistent tracing coverage counts
# as incomplete observability, and "every function you write" means both
# layers, not whichever one is more convenient. This does produce a nested
# ENTER/EXIT pair per call (one for the MCP-facing wrapper, one for the
# logic function) rather than a single flat one — that's intentional: it's
# the same call-chain visibility any layered tracing setup gives you, and it
# means a trace of a live MCP call shows the full path from tool invocation
# down into business logic, not just the inner half of it.


@mcp.tool()
@trace(logger, redact=guardrails.redact_for_logging)
def get_account_summary(account_id: str, caller_scope: str) -> dict:
    """Look up a NeoBank India account's summary (type, balance, status) by
    account_id. caller_scope must be one of: teller, loan_officer,
    compliance_officer, admin — controls which fields are returned. Account
    number is always masked to the last 4 digits, regardless of scope."""
    return logic.get_account_summary(account_id, caller_scope)


@mcp.tool()
@trace(logger, redact=guardrails.redact_for_logging)
def get_accounts_for_customer(customer_id: str, caller_scope: str) -> dict:
    """List every account belonging to customer_id, minimized per
    caller_scope the same way get_account_summary is."""
    return logic.get_accounts_for_customer(customer_id, caller_scope)


@mcp.tool()
@trace(logger, redact=guardrails.redact_for_logging)
def get_transaction_history(account_id: str, limit: int = 10) -> dict:
    """Return up to `limit` most recent transactions for account_id, most
    recent first (max 100)."""
    return logic.get_transaction_history(account_id, limit)


@mcp.resource("account://{account_id}/summary")
@trace(logger, redact=guardrails.redact_for_logging)
def account_summary(account_id: str) -> dict:
    """A single account's masked summary — no bulk data, no unmasked
    sensitive fields."""
    return logic.account_summary_resource(account_id)


@mcp.prompt()
@trace(logger)
def transaction_analysis_prompt(customer_name: str, account_type: str, analysis_period: str) -> str:
    """Reusable instruction template guiding the AI client through
    analyzing a customer's transaction history — never embeds real account
    data itself; it points at the Tool/Resource calls to make."""
    return logic.transaction_analysis_prompt(customer_name, account_type, analysis_period)


if __name__ == "__main__":
    db.init_db(force=False)
    logger.info("Starting accounts_server (stdio transport)")
    mcp.run(transport="stdio")
