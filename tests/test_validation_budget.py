"""Paid probe allowance tests run without any provider requests."""
from concurrent.futures import ThreadPoolExecutor

import pytest
from validation_budget import ValidationBudget, ValidationLease, measured, safe_usage


@pytest.mark.parametrize("usage,expected", [
    ({"input_tokens": 100, "input_tokens_details": {"cached_tokens": 80}, "output_tokens": 999}, (100, 80, 999)),
    ({"prompt_tokens": 100, "prompt_tokens_details": {"cached_tokens": 20}}, (100, 20, None)),
    ({"prompt_tokens": 100, "prompt_cache_hit_tokens": 90}, (100, 90, None)),
    ({"input_tokens": 100}, (100, 0, None)),
    ({"input_tokens": 0}, (0, 0, None)),
    ({"input_tokens": 20, "input_tokens_details": {"cached_tokens": 21}}, None),
    ({"input_tokens": True}, None),
    ({"total_tokens": 100}, None),
])
def test_native_usage(usage, expected):
    assert measured(usage) == expected


def test_shared_allowance_persistence_unknown_and_discount(tmp_path):
    path = tmp_path / "ledger.json"
    budget = ValidationBudget(path, limit=2200)
    with ThreadPoolExecutor(4) as executor:
        rows = list(executor.map(lambda _: budget.reserve(b"x"), range(4)))
    assert sum(r is not None for r in rows) == 2
    a, b = [r for r in rows if r]
    budget.settle(a, 200, {"input_tokens": 1000, "input_tokens_details": {"cached_tokens": 900}, "output_tokens": 99999}, True)
    budget.settle(b, 200, {"input_tokens": 1}, False)
    assert budget.used() == 190 + 1025
    assert ValidationBudget(path, limit=2200).used() == 1215
    assert budget.reserve(b"x") is None
    budget.settle(b, 200, {"input_tokens": 1}, True)
    assert budget.used() == 191
    assert budget.reserve(b"x") is not None


def test_usage_sanitized_and_authorization_cannot_be_reset(tmp_path):
    assert safe_usage({"input_tokens": 12, "secret": "discard", "input_tokens_details": {"cached_tokens": 3, "text": "discard"}}) == {
        "input_tokens": 12, "input_tokens_details": {"cached_tokens": 3}}
    budget = ValidationBudget(tmp_path / "ledger.json", limit=2200)
    row = budget.reserve(b"x")
    budget.settle(row, 500, {"input_tokens": 1}, True)
    assert budget.used() == 1025
    with pytest.raises(ValueError):
        ValidationBudget(budget.path, limit=10000)


def test_paid_relay_exclusive_lease_released_after_close(tmp_path):
    path = tmp_path / "round.json"
    first = ValidationLease(path)
    try:
        with pytest.raises(RuntimeError, match="already owns"):
            ValidationLease(path)
    finally:
        first.close()
    ValidationLease(path).close()


def test_unexpected_provider_input_blocks_uncalibrated_further_calls(tmp_path):
    budget = ValidationBudget(tmp_path / "round.json", limit=10000)
    row = budget.reserve(b"x")
    budget.settle(row, 200, {"input_tokens": row["reserved"] + 1}, True)
    assert budget.reserve(b"x") is None
    assert ValidationBudget(budget.path, limit=10000).data["blocked_reason"]
