"""
compliance_comms_server — FastMCP registration & entry point.

    pip install mcp
    python -m compliance_comms_server              # stdio transport
    mcp dev compliance_comms_server/__main__.py    # MCP Inspector

Runs as its own standalone local process — no Docker. products_server
composes with this server's logic.py in-process for the KYC gate and
audit-log write inside submit_loan_application — see
products_server/logic.py's module docstring for why.
"""
from __future__ import annotations

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import guardrails
from compliance_comms_server import db, logic
from logging_config import get_logger, trace

from mcp.server.fastmcp import FastMCP  # pip install mcp

logger = get_logger("compliance_comms_server.server")

mcp = FastMCP("neobank-compliance-comms-server")

# Every @mcp.tool()/@mcp.resource()/@mcp.prompt() function below is also
# @trace(logger)'d directly, in addition to the logic.py function it
# delegates to — see accounts_server/__main__.py's module docstring for why
# both layers are traced rather than just one.


@mcp.tool()
@trace(logger, redact=guardrails.redact_for_logging)
def get_kyc_status(customer_id: str) -> dict:
    """The only tool anywhere returning raw KYC data."""
    return logic.get_kyc_status(customer_id)


@mcp.tool()
@trace(logger, redact=guardrails.redact_for_logging)
def run_compliance_check(customer_id: str, transaction_amount: float) -> dict:
    """Deterministic pass/block + large-transaction reporting flag."""
    return logic.run_compliance_check(customer_id, transaction_amount)


@mcp.tool()
@trace(logger, redact=guardrails.redact_for_logging)
def get_fraud_flags(customer_id: str) -> dict:
    """Read operation over the fraud_flags table."""
    return logic.get_fraud_flags(customer_id)


@mcp.tool()
@trace(logger, redact=guardrails.redact_for_logging)
def write_audit_log(action_type: str, performed_by: str, customer_id: str,
                     outcome: str, details: str = "") -> dict:
    """Writes one audit_log entry (timestamp, action type, performed-by,
    customer ID, outcome) for a write operation."""
    return logic.write_audit_log(action_type, performed_by, customer_id, outcome, details)


@mcp.tool()
@trace(logger, redact=guardrails.redact_for_logging)
def send_customer_communication(customer_id: str, channel: str, message: str) -> dict:
    """Sanitises input, enforces the KYC-based comms rule, sends, and audit-logs."""
    return logic.send_customer_communication(customer_id, channel, message)


@mcp.tool()
@trace(logger, redact=guardrails.redact_for_logging)
def generate_customer_communication(template_name: str, customer_name: str,
                                     account_tier: str, specific_detail: str) -> dict:
    """Renders a filesystem Markdown template (communication_templates/) into
    message text. Read-only — does not send or log."""
    return logic.generate_customer_communication(template_name, customer_name, account_tier, specific_detail)


@mcp.resource("customer://{customer_id}/profile")
@trace(logger, redact=guardrails.redact_for_logging)
def customer_profile(customer_id: str) -> dict:
    """A single customer's profile — PII fields masked, no bulk data."""
    return logic.customer_profile_resource(customer_id)


@mcp.resource("template://{template_name}")
@trace(logger)
def template(template_name: str) -> dict:
    """Raw Markdown template content, so a client can see what fields it needs."""
    return logic.template_resource(template_name)


@mcp.prompt()
@trace(logger)
def customer_communication_prompt(template_name: str, customer_name: str,
                                   account_tier: str, specific_detail: str) -> str:
    """Reusable instruction template directing the AI client to
    generate_customer_communication then send_customer_communication, in
    order — never embeds real customer data itself."""
    return logic.customer_communication_prompt(template_name, customer_name, account_tier, specific_detail)


if __name__ == "__main__":
    db.init_db(force=False)
    logger.info("Starting compliance_comms_server (stdio transport)")
    mcp.run(transport="stdio")
