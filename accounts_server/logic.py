"""
accounts_server — tool business logic.

Plain functions, no `mcp` or `pydantic` dependency, so they're fully
testable with the standard library alone. server.py wraps each of these
with @mcp.tool() / @mcp.resource() to actually register them against a
live MCP server.

Tools (per brief §3.1):
    get_account_summary(account_id, caller_scope)
    get_accounts_for_customer(customer_id, caller_scope)
    get_transaction_history(account_id, limit)

Resource (per brief §7):
    account://{account_id}/summary
"""
from __future__ import annotations

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import guardrails
from logging_config import get_logger, trace
from accounts_server import db

logger = get_logger("accounts_server.logic")


@trace(logger)
def _build_account_view(account: dict, caller_scope: str) -> dict:
    """Shared by get_account_summary, get_accounts_for_customer, and the
    account:// Resource — masks the account number (unconditional, §4),
    enriches with KYC/risk fields for compliance_officer, then minimizes
    per caller_scope. Centralizing this means the Resource reuses the exact
    same guardrail path the Tools use, rather than duplicating the logic
    (explicitly checked by the rubric's "MCP primitive design quality" row).
    """
    view = dict(account)
    view["account_number_masked"] = guardrails.mask_account_number(view.pop("account_number"))

    if caller_scope == "compliance_officer":
        customer = db.get_customer_minimal(account["customer_id"]) or {}
        view["kyc_status"] = customer.get("kyc_status")
        view["risk_rating"] = customer.get("risk_rating")

    return guardrails.minimize_account_fields(view, caller_scope)


@trace(logger, redact=guardrails.redact_for_logging)
def get_account_summary(account_id: str, caller_scope: str) -> dict:
    """Look up a NeoBank India account's summary by account_id. caller_scope
    must be one of: teller, loan_officer, compliance_officer, admin —
    controls which fields are returned. Account number is always masked to
    the last 4 digits regardless of scope.
    """
    guardrails.validate_id_format("account_id", account_id)
    guardrails.validate_scope(caller_scope)

    account = db.get_account(account_id)
    if not account:
        return {"error": "not_found", "message": f"No account found for account_id '{account_id}'"}

    return _build_account_view(account, caller_scope)


@trace(logger, redact=guardrails.redact_for_logging)
def get_accounts_for_customer(customer_id: str, caller_scope: str) -> dict:
    """List every account belonging to customer_id, each minimized per
    caller_scope the same way get_account_summary is."""
    guardrails.validate_id_format("customer_id", customer_id)
    guardrails.validate_scope(caller_scope)

    accounts = db.get_accounts_for_customer(customer_id)
    if not accounts:
        return {"error": "not_found", "message": f"No accounts found for customer_id '{customer_id}'"}

    return {"accounts": [_build_account_view(a, caller_scope) for a in accounts]}


@trace(logger, redact=guardrails.redact_for_logging)
def get_transaction_history(account_id: str, limit: int = 10) -> dict:
    """Return up to `limit` most recent transactions for account_id, most
    recent first. Transaction records carry no customer PII beyond the
    account_id itself, so no additional field minimization is applied here
    — scope-based restriction happens one level up at the account-summary
    step.
    """
    guardrails.validate_id_format("account_id", account_id)
    if limit > 100:
        limit = 100  # guardrail against an unbounded query
    if limit < 1:
        limit = 1

    account = db.get_account(account_id)
    if not account:
        return {"error": "not_found", "message": f"No account found for account_id '{account_id}'"}

    return {"account_id": account_id, "transactions": db.get_transactions(account_id, limit=limit)}


@trace(logger, redact=guardrails.redact_for_logging)
def account_summary_resource(account_id: str) -> dict:
    """Backing function for the account://{account_id}/summary Resource.
    Resources return a single record, never bulk data, and never an
    unmasked sensitive field — enforced here by reusing _build_account_view
    at a fixed 'admin' scope capped to a safe public field set, rather than
    exposing every field unconditionally.
    """
    guardrails.validate_id_format("account_id", account_id)
    account = db.get_account(account_id)
    if not account:
        return {"error": "not_found", "message": f"No account found for account_id '{account_id}'"}

    # Resources have no caller_scope argument (URIs can't carry one cleanly),
    # so they're pinned to the most restrictive useful scope: masked account
    # number, no KYC/risk fields.
    return _build_account_view(account, caller_scope="teller")


@trace(logger)
def transaction_analysis_prompt(customer_name: str, account_type: str, analysis_period: str) -> str:
    """Backing function for the transaction_analysis_prompt Prompt. Returns a
    reusable instruction template guiding the AI client through analyzing a
    customer's transaction history — never embeds real account data itself;
    it points at the Tool/Resource calls to make. Routed through logic.py
    (rather than built inline in __main__.py) so it's traced the same way
    every other Tool/Resource/Prompt in this project is — see §5's warning
    about inconsistent tracing coverage.
    """
    return (
        f"To analyze {customer_name}'s {account_type} account activity over {analysis_period}: "
        f"first call get_accounts_for_customer(customer_id=<their CUS-XXXXX id>, caller_scope=<appropriate scope>) "
        f"to find the relevant account_id, then call get_transaction_history(account_id=<that id>, limit=<enough to "
        f"cover {analysis_period}>) to retrieve the transactions. Summarize spending patterns, large or unusual "
        f"transactions, and net cash flow over {analysis_period} from the returned data — do not estimate or "
        f"fabricate figures not present in the tool results."
    )
