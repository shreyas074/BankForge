from __future__ import annotations

from tests import _bootstrap  # noqa: F401
from accounts_server import logic
import guardrails


def test_get_account_summary_masks_account_number():
    result = logic.get_account_summary("ACC-10042", "teller")
    assert result["account_number_masked"].endswith("9042")
    assert result["account_number_masked"].startswith("*")


def test_get_account_summary_teller_scope_excludes_kyc_fields():
    result = logic.get_account_summary("ACC-10042", "teller")
    assert "kyc_status" not in result
    assert "risk_rating" not in result


def test_get_account_summary_compliance_officer_scope_includes_kyc_fields():
    result = logic.get_account_summary("ACC-10042", "compliance_officer")
    assert result["kyc_status"] == "verified"
    assert result["risk_rating"] == "low"


def test_get_account_summary_not_found():
    result = logic.get_account_summary("ACC-99999", "teller")
    assert result["error"] == "not_found"


def test_get_account_summary_invalid_format_raises():
    try:
        logic.get_account_summary("ACC0042", "teller")
        assert False, "expected InvalidIDFormatError"
    except guardrails.InvalidIDFormatError:
        pass


def test_get_accounts_for_customer_returns_all_accounts():
    result = logic.get_accounts_for_customer("CUS-10042", "teller")
    assert len(result["accounts"]) == 1
    assert result["accounts"][0]["account_id"] == "ACC-10042"


def test_get_transaction_history_most_recent_first():
    result = logic.get_transaction_history("ACC-10042", limit=5)
    dates = [t["txn_date"] for t in result["transactions"]]
    assert dates == sorted(dates, reverse=True)


def test_get_transaction_history_limit_capped_at_100():
    result = logic.get_transaction_history("ACC-10042", limit=99999)
    assert len(result["transactions"]) <= 100


def test_account_resource_matches_teller_view():
    resource_view = logic.account_summary_resource("ACC-10042")
    tool_view = logic.get_account_summary("ACC-10042", "teller")
    assert resource_view == tool_view
