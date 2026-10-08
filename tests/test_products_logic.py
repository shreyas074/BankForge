from __future__ import annotations

from tests import _bootstrap  # noqa: F401
from products_server import logic


def test_list_loan_products_filters_by_category():
    result = logic.list_loan_products("personal")
    assert all(p["category"] == "personal" for p in result["products"])
    assert len(result["products"]) == 1


def test_get_loan_product_details_not_found():
    result = logic.get_loan_product_details("PROD-ZZ-99")
    assert result["error"] == "not_found"


def test_eligibility_low_risk_passes_medium_threshold_product():
    result = logic.check_eligibility_criteria("PROD-PL-01", "low")
    assert result["eligible"] is True


def test_eligibility_high_risk_fails_medium_threshold_product():
    result = logic.check_eligibility_criteria("PROD-PL-01", "high")
    assert result["eligible"] is False


def test_eligibility_rejects_bad_product_id_format():
    import guardrails
    try:
        logic.check_eligibility_criteria("LOAN_PERSONAL_01", "low")
        assert False, "expected InvalidIDFormatError"
    except guardrails.InvalidIDFormatError:
        pass


def test_submit_loan_application_happy_path():
    result = logic.submit_loan_application("CUS-10042", "PROD-PL-01", 250000.0)
    assert result["status"] == "submitted"
    assert result["application_id"].startswith("APP-")


def test_submit_loan_application_exceeds_max_amount_rejected():
    result = logic.submit_loan_application("CUS-10042", "PROD-PL-01", 999999999.0)
    assert result["status"] == "rejected"


def test_submit_loan_application_blocked_for_non_verified_kyc():
    result = logic.submit_loan_application("CUS-10003", "PROD-PL-01", 50000.0)
    assert result["status"] == "rejected"
    assert "KYC" in result["reason"]


def test_submit_loan_application_blocked_for_ineligible_risk():
    # CUS-10004 is verified but risk_rating=high, which exceeds PROD-PL-01's
    # medium threshold.
    result = logic.submit_loan_application("CUS-10004", "PROD-PL-01", 50000.0)
    assert result["status"] == "rejected"


def test_get_loan_application_status_roundtrip():
    submitted = logic.submit_loan_application("CUS-10042", "PROD-HL-01", 500000.0)
    status = logic.get_loan_application_status(submitted["application_id"])
    assert status["status"] == "submitted"
    assert status["product_id"] == "PROD-HL-01"


def test_get_loan_application_status_not_found():
    result = logic.get_loan_application_status("APP-00000")
    assert result["error"] == "not_found"
