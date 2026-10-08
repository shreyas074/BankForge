"""
products_server — tool business logic.

Plain functions, no `mcp`/`pydantic` dependency. server.py wraps these
with @mcp.tool() / @mcp.resource().

Tools (per brief §3.1):
    list_loan_products(category)
    get_loan_product_details(product_id)
    check_eligibility_criteria(product_id, applicant_risk_rating)
    submit_loan_application(customer_id, product_id, requested_amount)  -- Write, KYC-gated
    get_loan_application_status(application_id)

Resource (per brief §7):
    product://{product_id}/details

--- The one deliberate seam between servers ---

submit_loan_application must independently verify KYC status and write an
audit trail entry, and products_server's own database has no customer or
KYC data at all (by construction — see db.py). Both checks are performed
by calling compliance_comms_server's own tool-logic functions directly:

    from compliance_comms_server import logic as compliance_logic
    compliance_logic.get_kyc_status(customer_id)
    compliance_logic.write_audit_log(...)

This is the same code path a live MCP client would hit calling those tools
over the wire — here it's an in-process call so run_local_demo.py can
exercise the full flow without needing both servers running as separate
processes (per brief §8: the demo script must not require a live MCP
client). When all three servers run as independent processes, this
function would instead open an MCP client connection to the running
compliance_comms_server process; the business logic and the fail-closed
behavior below are identical either way — only the transport changes.

This import is deliberately narrow and one-directional: products_server
reaches into compliance_comms_server's *logic* module (never its db.py),
and no other module in this project does the reverse.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import guardrails
from logging_config import get_logger, trace
from products_server import db

logger = get_logger("products_server.logic")

# The one cross-server import in the project. See module docstring.
from compliance_comms_server import logic as compliance_logic


@trace(logger)
def list_loan_products(category: str | None = None) -> dict:
    return {"products": db.list_products(category)}


@trace(logger)
def get_loan_product_details(product_id: str) -> dict:
    guardrails.validate_id_format("product_id", product_id)
    product = db.get_product(product_id)
    if not product:
        return {"error": "not_found", "message": f"No loan product found for product_id '{product_id}'"}
    return product


@trace(logger)
def check_eligibility_criteria(product_id: str, applicant_risk_rating: str) -> dict:
    """Criteria check only — no write, no customer data. Takes
    applicant_risk_rating as an explicit argument rather than a customer_id,
    so this check never requires products_server to see a real customer
    record at all."""
    guardrails.validate_id_format("product_id", product_id)
    if applicant_risk_rating not in ("low", "medium", "high"):
        return {"error": "invalid_input", "message": "applicant_risk_rating must be one of: low, medium, high"}

    product = db.get_product(product_id)
    if not product:
        return {"error": "not_found", "message": f"No loan product found for product_id '{product_id}'"}

    applicant_rank = db.RISK_ORDER[applicant_risk_rating]
    threshold_rank = db.RISK_ORDER[product["max_acceptable_risk_rating"]]
    eligible = applicant_rank <= threshold_rank

    reason = (
        f"applicant risk '{applicant_risk_rating}' is within '{product['max_acceptable_risk_rating']}' threshold"
        if eligible else
        f"applicant risk '{applicant_risk_rating}' exceeds this product's '{product['max_acceptable_risk_rating']}' threshold"
    )
    return {"product_id": product_id, "eligible": eligible, "reason": reason}


@trace(logger, redact=guardrails.redact_for_logging)
def submit_loan_application(customer_id: str, product_id: str, requested_amount: float) -> dict:
    """Write operation. Enforces the KYC gate at the Tool level (§4) —
    independently checked here, not left to the AI client's say-so — and
    writes an audit log entry for every outcome (approved, rejected, or the
    compliance-service-unavailable case), composing with
    compliance_comms_server for both. Fails closed: if the KYC check itself
    cannot be performed, the application is rejected, not silently allowed.
    """
    guardrails.validate_id_format("customer_id", customer_id)
    guardrails.validate_id_format("product_id", product_id)

    product = db.get_product(product_id)
    if not product:
        reason = f"No loan product found for product_id '{product_id}'"
        _record_outcome(customer_id, product_id, requested_amount, "rejected", reason)
        return {"error": "not_found", "message": reason}
    if not isinstance(requested_amount, (int, float)) or requested_amount <= 0:
        reason = "requested_amount must be a positive number"
        _record_outcome(customer_id, product_id, requested_amount, "rejected", reason)
        return {"error": "invalid_input", "message": reason}
    if requested_amount > product["max_amount"]:
        reason = f"requested_amount {requested_amount} exceeds product max_amount {product['max_amount']}"
        _record_outcome(customer_id, product_id, requested_amount, "rejected", reason)
        return {"status": "rejected", "reason": reason}

    # --- Cross-server composition: KYC check (fails closed on error) ---
    try:
        kyc = compliance_logic.get_kyc_status(customer_id)
    except Exception as exc:  # pragma: no cover - defensive, see offline Stress Test
        logger.error(f"Compliance service unreachable during submit_loan_application: {exc}")
        # No _record_outcome here, deliberately: the audit write path goes
        # through this exact same compliance_logic module (see
        # _record_outcome below), so if it's unreachable the audit write
        # would fail too — the response already surfaces that honestly via
        # audit_log_write_failed rather than silently swallowing it.
        return {
            "status": "rejected",
            "reason": "Compliance service unavailable — cannot verify KYC, application not submitted",
            "audit_log_write_failed": True,
        }

    if kyc.get("error") == "not_found":
        reason = f"No customer found for customer_id '{customer_id}'"
        _record_outcome(customer_id, product_id, requested_amount, "rejected", reason)
        return {"status": "rejected", "reason": reason}

    allowed, reason = guardrails.can_submit_loan_application(kyc["kyc_status"])
    if not allowed:
        _record_outcome(customer_id, product_id, requested_amount, "rejected", reason)
        return {"status": "rejected", "reason": reason}

    eligibility = check_eligibility_criteria(product_id, kyc["risk_rating"])
    if not eligibility.get("eligible", False):
        _record_outcome(customer_id, product_id, requested_amount, "rejected", eligibility["reason"])
        return {"status": "rejected", "reason": eligibility["reason"]}

    application_id = f"APP-{uuid.uuid4().hex[:5].upper()}"
    submitted_at = datetime.now(timezone.utc).isoformat()
    db.insert_application(application_id, customer_id, product_id, requested_amount,
                           "submitted", submitted_at, "eligible, KYC verified")
    _record_outcome(customer_id, product_id, requested_amount, "submitted",
                     f"application_id={application_id}")

    return {"status": "submitted", "application_id": application_id, "submitted_at": submitted_at}


def _record_outcome(customer_id: str, product_id: str, requested_amount: float,
                     outcome: str, reason: str) -> None:
    """Writes the audit trail entry via compliance_comms_server, degrading
    gracefully (logged locally, flagged in the tool result) rather than
    crashing the whole submission if the audit write itself can't reach
    compliance_comms_server."""
    try:
        compliance_logic.write_audit_log(
            action_type="submit_loan_application", performed_by="system",
            customer_id=customer_id, outcome=outcome,
            details=f"product_id={product_id}, requested_amount={requested_amount}, reason={reason}",
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.error(f"Audit log write failed for submit_loan_application: {exc}")


@trace(logger, redact=guardrails.redact_for_logging)
def get_loan_application_status(application_id: str) -> dict:
    application = db.get_application(application_id)
    if not application:
        return {"error": "not_found", "message": f"No application found for application_id '{application_id}'"}
    return application


@trace(logger)
def product_details_resource(product_id: str) -> dict:
    """Backing function for product://{product_id}/details."""
    return get_loan_product_details(product_id)
