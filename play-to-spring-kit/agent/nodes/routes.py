"""Routes phase (M4 Task 1) — runs once after the slice pipeline finishes,
before the cross-module verify pass.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable

from ..agents.routes import run_routes_agent
from ..config import AgentConfig
from ..state import RUN_OUTCOME_EXIT_CODES, MigrationState
from ..status_v2 import atomic_write_json
from ..tools.routes_parser import diff_routes, find_spring_mappings, parse_routes_file
from .common import phase_budget_decision

if TYPE_CHECKING:
    from ..graph import RuntimeCtx

LOG = logging.getLogger("agent.nodes.routes")


def _write_route_map(config: AgentConfig, mapped: list[dict], unmapped: list[dict]) -> dict:
    route_map = {"mapped": mapped, "unmapped": unmapped}
    atomic_write_json(config.migration_dir / "route-map.json", route_map)
    return route_map


def route_after_routes_node(state: MigrationState) -> str:
    if state.get("run_outcome"):
        return "halt"
    decision = state.get("routes_decision")
    if decision == "loop":
        return "routes"
    if decision == "noop":
        # No routes to map at all: skip the compile-fix re-entry entirely
        # (routes_fix_prep would otherwise reset the current `outcome`,
        # clobbering the slice pipeline's terminal outcome for no reason).
        return "config_mapping"
    return "routes_fix_prep"


def routes_fix_prep_node(state: MigrationState) -> dict:
    # Mirrors slice_router_node's per-slice reset dict exactly, plus the
    # generic fix-cycle markers (phase / fix_cycle_return_to) that
    # retarget done/infra/halt to after_fix_cycle and tell it where to
    # hand control back once the cycle ends non-fatally. Any future
    # phase that needs the same "re-enter compile-fix, then come back"
    # pattern adds its own <phase>_fix_prep node setting the same two
    # generic fields — after_fix_cycle/route_after_fix_cycle need no
    # per-phase changes.
    return {
        "retry_count": 0,
        "error_fingerprints": [],
        "det_fix_log": [],
        "last_compile": {},
        "last_clusters": [],
        "last_edited_files": [],
        "det_fixed_last_round": 0,
        "slice_started_at": 0.0,
        "guard_decision": None,
        "outcome": None,
        "exit_code": None,
        "phase": "fix_cycle",
        "fix_cycle_return_to": "config_mapping",
        "slice_id": "__routes__",
    }


def build(config: AgentConfig, ctx: "RuntimeCtx") -> dict[str, Callable]:
    def routes_node(state: MigrationState) -> dict:
        attempts = state.get("routes_attempts", 0)
        if attempts == 0:
            routes_file = (config.play_repo / "conf" / "routes") if config.play_repo is not None else None
            if routes_file is None or not routes_file.is_file():
                # Nothing to map (no Play repo, or no conf/routes) — no-op.
                route_map = _write_route_map(config, [], [])
                return {"route_map": route_map, "routes_decision": "noop"}

        # Recompute unmapped fresh every round: the previous agent round may
        # have mapped some (or all) of the previously-unmapped routes.
        routes = parse_routes_file(config.play_repo / "conf" / "routes")
        spring_mappings = find_spring_mappings(config.spring_repo)
        mapped, unmapped = diff_routes(routes, spring_mappings)

        if not unmapped:
            route_map = _write_route_map(config, mapped, unmapped)
            return {"route_map": route_map, "routes_decision": "proceed"}

        # Shared three-way decision (budget checked before the per-phase
        # attempts cap — see phase_budget_decision) used by every bounded
        # per-phase LLM loop.
        decision = phase_budget_decision(state, config, attempts, config.max_routes_attempts)
        if decision == "budget_exhausted":
            LOG.warning("routes: global LLM budget exhausted, aborting run")
            route_map = _write_route_map(config, mapped, unmapped)
            return {
                "route_map": route_map,
                "run_outcome": "budget_exhausted",
                "run_exit_code": RUN_OUTCOME_EXIT_CODES["budget_exhausted"],
            }
        if decision == "attempts_exhausted":
            LOG.warning("routes: giving up after %d attempts, %d routes still unmapped", attempts, len(unmapped))
            route_map = _write_route_map(config, mapped, unmapped)
            return {"route_map": route_map, "routes_decision": "proceed"}

        run_routes_agent(config, unmapped, attempt=attempts + 1, model_override=ctx.model_override)
        return {
            "routes_attempts": attempts + 1,
            "total_llm_calls": state.get("total_llm_calls", 0) + 1,
            "routes_decision": "loop",
        }

    return {
        "routes_node": routes_node,
    }
