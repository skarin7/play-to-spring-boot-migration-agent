"""pricing.py tests: rate table, env overrides, cost computation, and the
unknown-model-never-raises guarantee (M6 Task 5)."""

import json

from agent import pricing


def test_known_model_cost_computed():
    cost = pricing.cost_usd("anthropic/claude-haiku-4.5", 1_000_000, 1_000_000)
    assert cost == 1.0 + 5.0  # input rate + output rate, both per-million


def test_unknown_model_returns_none_not_raises():
    cost = pricing.cost_usd("some/made-up-model", 1000, 1000)
    assert cost is None


def test_cache_read_billed_at_discount(monkeypatch):
    # 100 input tokens, all cache reads -- should cost less than treating
    # them as regular input tokens, at the documented 10x discount ratio.
    full_price = pricing.cost_usd("anthropic/claude-haiku-4.5", 100, 0)
    discounted = pricing.cost_usd("anthropic/claude-haiku-4.5", 100, 0, cache_read_input_tokens=100)
    assert discounted < full_price
    assert abs(discounted - full_price / 10) < 1e-9


def test_cache_read_tokens_capped_at_input_tokens():
    """cache_read_input_tokens larger than input_tokens (a caller bug) must
    not produce a negative regular_input count."""
    cost = pricing.cost_usd("anthropic/claude-haiku-4.5", 100, 0, cache_read_input_tokens=1_000_000)
    # All 100 tokens billed at the cache rate, nothing negative/absurd.
    rate = pricing.rate_table()["anthropic/claude-haiku-4.5"]
    expected = 100 * rate.effective_cache_read_rate() / 1_000_000
    assert abs(cost - expected) < 1e-9


def test_env_override_replaces_default_rate(monkeypatch):
    monkeypatch.setenv(
        "MIGRATION_PRICING_JSON",
        json.dumps({"anthropic/claude-haiku-4.5": {"input_per_million": 2.0, "output_per_million": 10.0}}),
    )
    cost = pricing.cost_usd("anthropic/claude-haiku-4.5", 1_000_000, 1_000_000)
    assert cost == 2.0 + 10.0


def test_env_override_adds_new_model(monkeypatch):
    monkeypatch.setenv(
        "MIGRATION_PRICING_JSON",
        json.dumps({"some/new-model": {"input_per_million": 1.0, "output_per_million": 1.0}}),
    )
    cost = pricing.cost_usd("some/new-model", 1_000_000, 0)
    assert cost == 1.0


def test_env_override_malformed_json_ignored(monkeypatch):
    monkeypatch.setenv("MIGRATION_PRICING_JSON", "not json{{{")
    # Falls back to the default table rather than raising.
    cost = pricing.cost_usd("anthropic/claude-haiku-4.5", 1_000_000, 0)
    assert cost == 1.0


def test_env_override_malformed_entry_ignored(monkeypatch):
    monkeypatch.setenv(
        "MIGRATION_PRICING_JSON",
        json.dumps({"bad/model": {"input_per_million": "not-a-number"}}),
    )
    assert pricing.cost_usd("bad/model", 1000, 1000) is None


def test_no_env_override_uses_defaults(monkeypatch):
    monkeypatch.delenv("MIGRATION_PRICING_JSON", raising=False)
    cost = pricing.cost_usd("anthropic/claude-sonnet-4.5", 1_000_000, 1_000_000)
    assert cost == 3.0 + 15.0
