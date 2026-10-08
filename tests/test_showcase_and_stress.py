"""
Tests for the official Showcase Scenario (Priya Sharma / CUS-10042 /
PROD-PL-01) and all three official Stress Tests (brief §6, §8, §10):
missing customer, invalid loan submission, compliance server offline.
"""
from __future__ import annotations

from tests import _bootstrap  # noqa: F401
from accounts_server import logic as accounts_logic
from products_server import logic as products_logic
from compliance_comms_server import logic as compliance_logic


# --- Showcase Scenario: Priya Sharma / CUS-10042 / PROD-PL-01 ----------------

def test_showcase_scenario_end_to_end():
    customer_id, product_id = "CUS-10042", "PROD-PL-01"

    kyc = compliance_logic.get_kyc_status(customer_id)
    assert kyc["kyc_status"] == "verified"

    eligibility = products_logic.check_eligibility_criteria(product_id, kyc["risk_rating"])
    assert eligibility["eligible"] is True

    application = products_logic.submit_loan_application(customer_id, product_id, 250000.0)
    assert application["status"] == "submitted"

    status = products_logic.get_loan_application_status(application["application_id"])
    assert status["status"] == "submitted"
    assert status["customer_id"] == customer_id

    accounts = accounts_logic.get_accounts_for_customer(customer_id, "loan_officer")
    assert len(accounts["accounts"]) == 1
    account_id = accounts["accounts"][0]["account_id"]
    assert accounts["accounts"][0]["account_number_masked"].startswith("*")

    history = accounts_logic.get_transaction_history(account_id, limit=5)
    assert len(history["transactions"]) > 0

    rendered = compliance_logic.generate_customer_communication(
        "loan_approval", "Priya Sharma", "elite",
        f"Your {product_id} application {application['application_id']} has been received for review.",
    )
    assert application["application_id"] in rendered["rendered_message"]

    sent = compliance_logic.send_customer_communication(customer_id, "email", rendered["rendered_message"])
    assert sent["status"] == "sent"

    audit = compliance_logic.get_audit_log(customer_id)
    outcomes = {e["outcome"] for e in audit["audit_log"]}
    assert "submitted" in outcomes
    assert "sent" in outcomes


# --- Stress Test 1: missing customer -----------------------------------------

def test_stress_missing_customer_kyc_lookup():
    result = compliance_logic.get_kyc_status("CUS-99999")
    assert result["error"] == "not_found"  # graceful, not an unhandled exception


def test_stress_missing_customer_loan_submission():
    result = products_logic.submit_loan_application("CUS-99999", "PROD-PL-01", 100000.0)
    assert result["status"] == "rejected"
    assert "No customer found" in result["reason"]


# --- Stress Test 2: invalid loan submission ----------------------------------

def test_stress_invalid_loan_submission_non_verified_kyc():
    result = products_logic.submit_loan_application("CUS-10003", "PROD-PL-01", 50000.0)
    assert result["status"] == "rejected"
    assert "KYC" in result["reason"]


def test_stress_invalid_loan_submission_exceeds_max_amount():
    result = products_logic.submit_loan_application("CUS-10042", "PROD-PL-01", 999_999_999.0)
    assert result["status"] == "rejected"
    assert "max_amount" in result["reason"]


def test_stress_invalid_loan_submission_ineligible_risk_rating():
    # CUS-10004: verified KYC, but risk_rating=high exceeds PROD-PL-01's medium threshold.
    result = products_logic.submit_loan_application("CUS-10004", "PROD-PL-01", 50000.0)
    assert result["status"] == "rejected"


# --- Stress Test 3: compliance server offline --------------------------------

def _with_simulated_compliance_outage(exc: Exception, fn):
    """Manual patch/restore (no pytest fixture dependency) so this runs
    identically under `pytest tests/` and under the project's stdlib
    harness (run_tests.py)."""
    original = products_logic.compliance_logic.get_kyc_status

    def _simulate_outage(customer_id):
        raise exc

    products_logic.compliance_logic.get_kyc_status = _simulate_outage
    try:
        return fn()
    finally:
        products_logic.compliance_logic.get_kyc_status = original


def test_stress_compliance_server_offline_fails_closed():
    result = _with_simulated_compliance_outage(
        compliance_logic.ComplianceServiceUnavailableError("simulated outage"),
        lambda: products_logic.submit_loan_application("CUS-10042", "PROD-PL-01", 50000.0),
    )
    assert result["status"] == "rejected"
    assert "unavailable" in result["reason"].lower()
    assert result.get("audit_log_write_failed") is True


def test_stress_compliance_server_offline_does_not_silently_approve():
    """The critical fail-closed property: an outage must never be
    interpreted as 'KYC assumed fine.'"""
    result = _with_simulated_compliance_outage(
        ConnectionError("network unreachable"),
        lambda: products_logic.submit_loan_application("CUS-10042", "PROD-PL-01", 50000.0),
    )
    assert result["status"] != "submitted"
