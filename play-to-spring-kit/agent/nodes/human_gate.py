"""Human-in-the-loop gate for infrastructure compile errors (M5).

Headless (config.headless=True, the default — every automated/CI run):
a pure pass-through, behaviorally identical to routing straight to "infra"
as before this task existed. Interactive (--interactive sets
config.headless=False): pauses the graph via interrupt() so a human can
inspect the infra failure and choose to retry the compile (maybe it was a
transient toolchain crash) or accept it as a hard failure — same
finalization as headless either way once the decision is "abort".

Scope: infra errors only (per the architecture plan's "infra ->
(H)human_gate" diagram) -- other halt reasons (looping, retries_exhausted,
timeout, budget_exhausted) are unaffected by this task.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from langgraph.types import interrupt

from ..config import AgentConfig
from ..state import MigrationState

if TYPE_CHECKING:
    from ..graph import RuntimeCtx


def route_after_human_gate(state: MigrationState) -> str:
    return "compile" if state.get("human_gate_decision") == "retry" else "infra"


def build(config: AgentConfig, ctx: "RuntimeCtx") -> dict[str, Callable]:
    def human_gate_node(state: MigrationState) -> dict:
        if config.headless:
            # No interrupt() call at all in this branch -- required so that
            # every existing test (which compiles the graph without a
            # checkpointer) keeps passing unmodified.
            return {"human_gate_decision": "abort"}

        payload = {
            "reason": "infrastructure_error",
            "slice_id": state.get("slice_id"),
            "log_tail": state.get("last_compile", {}).get("log_tail", ""),
        }
        decision = interrupt(payload)
        normalized = decision if isinstance(decision, str) else str(decision)
        return {"human_gate_decision": "retry" if normalized.strip().lower() == "retry" else "abort"}

    return {
        "human_gate_node": human_gate_node,
    }
