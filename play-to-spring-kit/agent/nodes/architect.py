"""Architect phase nodes (M6 Task 6): runs once after inventory, before the
first slice transform. Writes .migration/decisions.md (dependency map, config
map, async policy, no_migration list, concerns) -- the cheapest correction
point in the run, and the cross-phase memory every later agent reads off
disk (path only, never contents pushed into their prompts).

Verification is deterministic (does decisions.md exist and is it non-empty?
-- see architect_verify_node), never the LLM's own self-report, matching
every other agent phase in this codebase.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from langgraph.types import interrupt

from ..agents.architect import DECISIONS_RELATIVE_PATH, run_architect
from ..config import AgentConfig
from ..state import RUN_OUTCOME_EXIT_CODES, MigrationState

if TYPE_CHECKING:
    from ..graph import RuntimeCtx

LOG = logging.getLogger("agent.nodes.architect")


def decisions_path(config: AgentConfig) -> Path:
    return config.spring_repo / DECISIONS_RELATIVE_PATH


def _decisions_present(config: AgentConfig) -> bool:
    p = decisions_path(config)
    return p.is_file() and p.stat().st_size > 0


def route_after_architect_check(state: MigrationState) -> str:
    # "skip" bypasses the approval gate entirely -- either there's no
    # play_repo to survey, or decisions.md already exists from a prior
    # session (resumed run). The gate is only for a decisions.md this
    # session's architect_agent just wrote (see route_after_architect_verify).
    return "skip" if state.get("architect_decision") == "skip" else "architect_agent"


def route_after_architect_verify(state: MigrationState) -> str:
    decision = state.get("architect_decision", "exhausted")
    return {
        "skip": "gate",
        "retry": "architect_agent",
        "exhausted": "run_halt",
    }[decision]


def route_after_architect_gate(state: MigrationState) -> str:
    return "inventory_done" if state.get("architect_decision") == "approved" else "run_halt"


def build(config: AgentConfig, ctx: "RuntimeCtx") -> dict[str, Callable]:
    def architect_check_node(state: MigrationState) -> dict:
        if config.play_repo is None:
            # No Play repo to survey (standalone mode against an
            # already-decided Spring repo) -- nothing for the architect to do.
            return {"architect_decision": "skip", "architect_attempts": state.get("architect_attempts", 0)}
        if _decisions_present(config):
            # Resumed run: decisions.md already written by a prior attempt.
            return {"architect_decision": "skip", "architect_attempts": state.get("architect_attempts", 0)}
        return {"architect_decision": "agent", "architect_attempts": state.get("architect_attempts", 0)}

    def architect_agent_node(state: MigrationState) -> dict:
        attempt = state.get("architect_attempts", 0) + 1
        run_architect(
            config,
            state.get("source_inventory"),
            state.get("play_surface_inventory"),
            attempt,
            model_override=ctx.architect_model_override,
        )
        return {"architect_attempts": attempt}

    def architect_verify_node(state: MigrationState) -> dict:
        if _decisions_present(config):
            return {"architect_decision": "skip"}
        if state.get("architect_attempts", 0) >= config.max_architect_attempts:
            LOG.error(
                "architect: giving up after %d attempts, %s not written",
                state.get("architect_attempts", 0), DECISIONS_RELATIVE_PATH,
            )
            return {
                "architect_decision": "exhausted",
                "run_outcome": "init_failed",
                "run_exit_code": RUN_OUTCOME_EXIT_CODES["init_failed"],
            }
        return {"architect_decision": "retry"}

    def architect_gate_node(state: MigrationState) -> dict:
        # Gate 1 (the plugin's terminology): the cheapest correction point in
        # the run -- everything downstream compiles against these decisions.
        # Headless never calls interrupt() at all -- required so every test
        # compiles the graph without a checkpointer (same invariant as
        # human_gate.py and the context-budget interrupt in llm.py).
        if config.headless:
            LOG.info("architect gate: headless mode, auto-approving decisions.md")
            return {"architect_decision": "approved"}

        payload = {
            "reason": "architect_review",
            "decisions_path": str(decisions_path(config)),
        }
        decision = interrupt(payload)
        normalized = decision if isinstance(decision, str) else str(decision)
        return {"architect_decision": "approved" if normalized.strip().lower() == "approve" else "revise"}

    return {
        "architect_check_node": architect_check_node,
        "architect_agent_node": architect_agent_node,
        "architect_verify_node": architect_verify_node,
        "architect_gate_node": architect_gate_node,
    }
