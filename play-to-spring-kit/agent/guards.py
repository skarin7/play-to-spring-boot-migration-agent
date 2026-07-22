"""Budget / loop / timeout guard — pure decision logic for the guard_check node."""

from __future__ import annotations

import time

from .config import AgentConfig
from .legacy_logic import is_looping
from .state import GuardDecision, MigrationState


def timed_out(state: MigrationState, config: AgentConfig, now: float | None = None) -> bool:
    """Wall-clock check, shared by compile_node (checked every loop iteration,
    matching the legacy top-of-while check at migration_orchestrator.py:2531)
    and guard_node (checked again before an LLM round)."""
    now = time.time() if now is None else now
    started = state.get("slice_started_at", 0.0)
    return bool(started) and (now - started) > config.timeout_layer_mins * 60


def decide(state: MigrationState, config: AgentConfig, now: float | None = None) -> GuardDecision:
    """Decide whether another LLM fix round is allowed.

    Order matters and mirrors the legacy loop: budget first (hard stop for the
    whole run), then per-slice retries/timeout, then fingerprint loop detection,
    then API-key availability.
    """
    if state.get("total_llm_calls", 0) >= config.max_total_llm_calls:
        return "budget_exhausted"

    if state.get("retry_count", 0) >= config.max_retries_per_layer:
        return "retries_exhausted"

    if timed_out(state, config, now):
        return "timeout"

    fingerprints = state.get("error_fingerprints", [])
    if len(fingerprints) >= 2 and is_looping(fingerprints[-1], fingerprints[:-1]):
        return "looping"

    if not config.api_key:
        return "no_llm"

    return "agent"
