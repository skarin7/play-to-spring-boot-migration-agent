"""Cross-phase shared mechanism for the generic fix-cycle re-entry pattern.

`_phase_budget_decision` is the shared three-way decision used by every
bounded per-phase LLM loop (routes, config_mapping, runtime_wiring, and any
future phase). `route_by_phase` / `after_fix_cycle_node` / `route_after_fix_cycle`
implement the generic "any phase can re-enter the M1 compile-fix subgraph and
come back" mechanism described in agent/graph.py's module docstring.

None of the functions in this module close over `config`/`ctx` -- they're
plain top-level functions (a phase's own budget check always passes `config`
in explicitly, and the fix-cycle bookkeeping only ever looks at `state`).
"""

from __future__ import annotations

import logging

from ..config import AgentConfig
from ..state import RUN_OUTCOME_EXIT_CODES, MigrationState

LOG = logging.getLogger("agent.nodes.common")


def _phase_budget_decision(
    state: MigrationState, config: AgentConfig, attempts: int, max_attempts: int
) -> str:
    """Returns "budget_exhausted" | "attempts_exhausted" | "continue" — shared by every
    bounded per-phase LLM loop (routes, config_mapping, and future phases). Global
    budget is checked before the per-phase attempts cap: the global LLM budget is a
    hard stop for the whole run, not just one phase, so it must be checked before
    every agent call, not only via a phase-local attempts cap.
    """
    if state.get("total_llm_calls", 0) >= config.max_total_llm_calls:
        return "budget_exhausted"
    if attempts >= max_attempts:
        return "attempts_exhausted"
    return "continue"


def route_by_phase(state: MigrationState) -> str:
    # done/infra/halt exit the compile-fix subgraph to different places
    # depending on which pipeline re-entered it (M2 slice loop vs any
    # phase's fix-cycle re-entry) — the nodes themselves are unchanged.
    # Always a plain two-way check regardless of how many phases exist:
    # after_fix_cycle is the single generic landing node for all of them
    # (see route_after_fix_cycle for the per-phase return-to routing).
    return "slice_finalize" if state.get("phase", "slice") == "slice" else "after_fix_cycle"


def after_fix_cycle_node(state: MigrationState) -> dict:
    outcome = state.get("outcome")
    if outcome == "budget_exhausted":
        # Legacy parity with slice_finalize_node's RUN_ABORTING_OUTCOMES
        # handling: budget exhaustion aborts the whole run right here.
        return {
            "run_outcome": "budget_exhausted",
            "run_exit_code": RUN_OUTCOME_EXIT_CODES["budget_exhausted"],
        }
    if outcome != "success":
        LOG.warning("fix cycle ended with outcome=%s (non-blocking)", outcome)
    return {"phase": "slice"}


def route_after_fix_cycle(state: MigrationState) -> str:
    if state.get("run_outcome"):
        return "halt"
    return state["fix_cycle_return_to"]
