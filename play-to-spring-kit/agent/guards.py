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

    # The agent itself already told us (via flag_for_manual_review, tools/fs.py)
    # that this needs a redesign, not more incremental attempts -- no point
    # burning further retries/tool calls re-discovering what it already found.
    if state.get("agent_manual_review_reason") is not None:
        return "looping"

    if state.get("retry_count", 0) >= config.max_retries_per_layer:
        return "retries_exhausted"

    if timed_out(state, config, now):
        return "timeout"

    fingerprints = state.get("error_fingerprints", [])
    if len(fingerprints) >= 2 and is_looping(fingerprints[-1], fingerprints[:-1]):
        return "looping"

    # cluster_node only runs when compile actually failed (route_after_compile
    # sends a clean compile straight to "done"), so last_compile.errors is
    # guaranteed non-empty here. An empty last_clusters therefore means every
    # remaining error's signature is already in excluded_error_signatures
    # (from a prior "looping" round) -- there is nothing left to put in an LLM
    # prompt. Without this check decide() falls through to "agent" and the
    # fix agent gets invoked with a blank error list: a wasted LLM round that
    # just explores files with no directive instead of surfacing the real
    # "stuck" state.
    if not state.get("last_clusters"):
        return "looping"

    if not config.api_key:
        return "no_llm"

    return "agent"
