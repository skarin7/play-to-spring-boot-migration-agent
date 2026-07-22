"""Runtime-wiring / boot-verification phase (M4 Task 3) — the ONE new phase
whose failure genuinely fails the whole run (see agent/graph.py's module
docstring). Runs after verify (compile-only) succeeds: actually starts the
Spring app and, if it never boots, invokes a bounded escalating-tier LLM
agent to fix wiring/config issues and retries, up to
config.max_runtime_wiring_attempts.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable

from ..agents.runtime_wiring import run_runtime_wiring_agent
from ..config import AgentConfig
from ..state import RUN_OUTCOME_EXIT_CODES, MigrationState
from .common import phase_budget_decision

if TYPE_CHECKING:
    from ..graph import RuntimeCtx

LOG = logging.getLogger("agent.nodes.boot")


def route_after_boot_run(state: MigrationState) -> str:
    return "final_verification" if state.get("boot_started") else "runtime_wiring"


def route_after_runtime_wiring_node(state: MigrationState) -> str:
    return "halt" if state.get("run_outcome") else "boot_run"


def build(config: AgentConfig, ctx: "RuntimeCtx") -> dict[str, Callable]:
    def boot_run_node(state: MigrationState) -> dict:
        if ctx.boot_runner is None:
            # No boot verification configured (every pre-M4-Task-3 test/caller
            # never wires a boot_runner) — treat as trivially started, mirroring
            # setup_node's `if ctx.setup_ops is None: return {}` skip-if-not-
            # configured convention.
            return {"boot_started": True, "boot_log_tail": ""}
        result = ctx.boot_runner(config)
        return {"boot_started": result.started, "boot_log_tail": result.log_tail}

    def runtime_wiring_node(state: MigrationState) -> dict:
        attempts = state.get("runtime_wiring_attempts", 0)
        decision = phase_budget_decision(state, config, attempts, config.max_runtime_wiring_attempts)
        if decision == "budget_exhausted":
            LOG.warning("runtime_wiring: global LLM budget exhausted, aborting run")
            return {
                "run_outcome": "budget_exhausted",
                "run_exit_code": RUN_OUTCOME_EXIT_CODES["budget_exhausted"],
            }
        if decision == "attempts_exhausted":
            LOG.warning("runtime_wiring: giving up after %d attempts, app never started", attempts)
            return {
                "run_outcome": "runtime_wiring_failed",
                "run_exit_code": RUN_OUTCOME_EXIT_CODES["runtime_wiring_failed"],
            }
        if not config.api_key:
            # Mirrors guard_node's "no_llm" check (agent/guards.py) for the M1
            # compile-fix subgraph: never let a missing API key crash the run
            # via make_model — degrade to a terminal outcome instead.
            LOG.warning("runtime_wiring: no API key configured, cannot invoke agent")
            return {"run_outcome": "no_llm", "run_exit_code": RUN_OUTCOME_EXIT_CODES["no_llm"]}
        run_runtime_wiring_agent(
            config, state.get("boot_log_tail", ""), attempt=attempts + 1, model_override=ctx.model_override
        )
        return {
            "runtime_wiring_attempts": attempts + 1,
            "total_llm_calls": state.get("total_llm_calls", 0) + 1,
        }

    return {
        "boot_run_node": boot_run_node,
        "runtime_wiring_node": runtime_wiring_node,
    }
