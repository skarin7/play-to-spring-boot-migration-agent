"""Per-model USD pricing (M6 Task 5).

Tokens were already logged in llm-usage.jsonl (llm.py's append_usage_log)
but never priced -- a MAX_TOTAL_LLM_CALLS=50 budget can be $0.40 or $40
depending on whether config.choose_model escalated to the premium tier and
how large the compacted transcripts grew. Call count is the wrong unit for a
spend cap; this module is what lets guards.py and nodes/common.py check a
dollar budget instead.

Rates are USD per million tokens, OpenRouter's own unit. Keyed by the exact
model id string used in AgentConfig.model_cheap/model_premium -- an unknown
model id returns None costs rather than raising, since a rate-table miss
(new model, provider renamed something) must never crash a migration run
that's otherwise working fine; it's logged once instead.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

LOG = logging.getLogger("agent.pricing")

_logged_unknown_models: set[str] = set()


@dataclass(frozen=True)
class ModelRate:
    input_per_million: float
    output_per_million: float
    # Cached-read tokens are billed at a steep discount vs. a fresh input
    # token (this is the whole point of prompt caching, M6 Task 4) --
    # defaults to input_per_million / 10, Anthropic's published ratio,
    # overridable per model in the rate table below.
    cache_read_per_million: float | None = None

    def effective_cache_read_rate(self) -> float:
        return self.cache_read_per_million if self.cache_read_per_million is not None else self.input_per_million / 10


# Default table, as of 2026-08-15. Prices drift; MIGRATION_PRICING_JSON below
# is the escape hatch for a rate change that doesn't require a code change.
_DEFAULT_RATES: dict[str, ModelRate] = {
    "anthropic/claude-haiku-4.5": ModelRate(input_per_million=1.0, output_per_million=5.0),
    "anthropic/claude-sonnet-4.5": ModelRate(input_per_million=3.0, output_per_million=15.0),
}


def _load_rate_overrides() -> dict[str, ModelRate]:
    raw = os.environ.get("MIGRATION_PRICING_JSON", "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        LOG.warning("MIGRATION_PRICING_JSON is not valid JSON; ignoring")
        return {}
    overrides: dict[str, ModelRate] = {}
    for model_id, rate in (parsed or {}).items():
        try:
            overrides[model_id] = ModelRate(
                input_per_million=float(rate["input_per_million"]),
                output_per_million=float(rate["output_per_million"]),
                cache_read_per_million=(
                    float(rate["cache_read_per_million"]) if "cache_read_per_million" in rate else None
                ),
            )
        except (KeyError, TypeError, ValueError):
            LOG.warning("MIGRATION_PRICING_JSON entry for %r is malformed; ignoring", model_id)
    return overrides


def rate_table() -> dict[str, ModelRate]:
    """Default rates with env overrides layered on top. Re-reads the env
    each call (cheap, and lets tests monkeypatch os.environ without needing
    to reach into module-level cache state)."""
    table = dict(_DEFAULT_RATES)
    table.update(_load_rate_overrides())
    return table


def cost_usd(
    model_id: str,
    input_tokens: int,
    output_tokens: int,
    *,
    cache_read_input_tokens: int = 0,
) -> float | None:
    """None on an unknown model id -- never raises, since a rate-table miss
    must not crash an otherwise-working run. cache_read_input_tokens are
    assumed already included in input_tokens (matches usage_metadata's
    shape) and are billed at the discounted rate instead of the full input
    rate, with the remainder billed normally."""
    rate = rate_table().get(model_id)
    if rate is None:
        if model_id not in _logged_unknown_models:
            _logged_unknown_models.add(model_id)
            LOG.warning("no pricing entry for model %r; costs for it will be logged as null", model_id)
        return None

    cache_read = min(cache_read_input_tokens, input_tokens)
    regular_input = input_tokens - cache_read

    return (
        regular_input * rate.input_per_million / 1_000_000
        + cache_read * rate.effective_cache_read_rate() / 1_000_000
        + output_tokens * rate.output_per_million / 1_000_000
    )
