"""Slice-pipeline nodes (M2): inventory + slice_router/transform/slice_finalize,
plus the cross-module verify pass and the terminal run_done/run_halt nodes.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable

from .. import inventory, legacy_logic
from ..config import AgentConfig
from ..state import (
    OUTCOME_EXIT_CODES,
    RUN_ABORTING_OUTCOMES,
    RUN_OUTCOME_EXIT_CODES,
    UNIT_TERMINAL_FAILURE_STATUSES,
    MigrationState,
)

if TYPE_CHECKING:
    from ..graph import RuntimeCtx

LOG = logging.getLogger("agent.nodes.slice_pipeline")


def _manual_intervention_note(state: MigrationState) -> str | None:
    """None unless there's a reason to believe the compile-fix loop is stuck
    on something that needs a redesign, not more auto-fix attempts.

    Prefers the agent's own diagnosis (agent_manual_review_reason, set via
    the flag_for_manual_review tool -- see tools/fs.py) since it generalizes
    to any framework/library mismatch the model recognizes, not just the
    ones this codebase happens to have a regex for. Falls back to the
    deterministic unmappable_framework_packages() guess (currently just
    Akka/Pekko) for older runs or a model that didn't call the tool."""
    reason = state.get("agent_manual_review_reason")
    if reason:
        return reason
    errors = state.get("last_compile", {}).get("errors", [])
    by_file = legacy_logic.unmappable_framework_packages(errors)
    if not by_file:
        return None
    parts = [f"{f} ({', '.join(sorted(pkgs))})" for f, pkgs in sorted(by_file.items())]
    return (
        "Automatic fixing stalled on package(s) with no Spring/Jakarta equivalent -- "
        "these file(s) need manual redesign, not an import/symbol fix: " + "; ".join(parts)
    )


def _log_play_surface_inventory(report: dict | None) -> None:
    """Surfaces the pre-flight scan's coverage/PARADIGM/UNKNOWN findings before any slice is
    transformed -- the point of Phase A: a known gap discovered up front, not a broken build.
    Gaps are logged at WARNING (not just recorded in state) so a headless run's own log makes
    the coverage picture visible without requiring a caller to inspect state after the fact."""
    if not report:
        return
    coverage = report.get("coveragePercent") or 0.0
    paradigm = report.get("paradigmCount", 0) or 0
    unknown = report.get("unknownCount", 0) or 0
    if not (paradigm or unknown):
        LOG.info("play-surface inventory: %.1f%% coverage, no gaps found", coverage)
        return
    LOG.warning(
        "play-surface inventory: %.1f%% coverage, %d paradigm, %d unknown construct(s) found before transform",
        coverage, paradigm, unknown,
    )
    for t in report.get("touchpoints", []):
        if t.get("classification") in ("PARADIGM", "UNKNOWN"):
            LOG.warning("  [%s] %s @ %s", t.get("classification"), t.get("construct"), t.get("location"))


def route_after_inventory(state: MigrationState) -> str:
    return "router" if state.get("migration_units") else "no_slices"


def slice_router_node(state: MigrationState) -> dict:
    units = state.get("migration_units") or []
    idx = state.get("current_unit_idx", 0)
    while idx < len(units) and units[idx].get("status") == "done":
        idx += 1
    if idx >= len(units):
        return {"current_unit_idx": idx}
    unit = units[idx]
    # Reset per-slice transient state (legacy parity: a fresh `le` scope
    # per entity_iterable iteration). total_llm_calls and
    # excluded_error_signatures are run-level and stay untouched.
    return {
        "current_unit_idx": idx,
        "slice_id": unit.get("id", ""),
        "retry_count": 0,
        "error_fingerprints": [],
        "det_fix_log": [],
        "last_compile": {},
        "last_clusters": [],
        "last_edited_files": [],
        "det_fixed_last_round": 0,
        "agent_manual_review_reason": None,
        "slice_started_at": 0.0,
        "guard_decision": None,
        "outcome": None,
        "exit_code": None,
    }


def route_after_slice_router(state: MigrationState) -> str:
    units = state.get("migration_units") or []
    idx = state.get("current_unit_idx", 0)
    return "transform" if idx < len(units) else "routes"


def route_after_slice_finalize(state: MigrationState) -> str:
    return "halt" if state.get("run_outcome") else "router"


def route_after_verify(state: MigrationState) -> str:
    units = state.get("migration_units") or []
    failed = any(u.get("status") in UNIT_TERMINAL_FAILURE_STATUSES for u in units)
    return "halt" if failed else "boot_run"


def run_done_node(state: MigrationState) -> dict:
    return {"run_outcome": "success", "run_exit_code": RUN_OUTCOME_EXIT_CODES["success"]}


def run_halt_node(state: MigrationState) -> dict:
    if state.get("run_outcome"):
        return {}
    units = state.get("migration_units") or []
    outcome = "no_slices" if not units else "slice_failures"
    return {"run_outcome": outcome, "run_exit_code": RUN_OUTCOME_EXIT_CODES[outcome]}


def build(config: AgentConfig, ctx: "RuntimeCtx") -> dict[str, Callable]:
    def inventory_node(state: MigrationState) -> dict:
        # Legacy parity: only discover if migration_units is empty (a resumed
        # run, or a caller that seeded units directly, keeps its units as-is —
        # migration_orchestrator.py :2412-2426 only (re)discovers when
        # status["migration_units"] is falsy or --refresh-inventory is set).
        if state.get("migration_units"):
            return {}
        if config.play_repo is None:
            # No Play repo configured: treat the whole Spring repo as one slice
            # (keeps `python -m agent --spring-repo` usable standalone).
            return {
                "migration_units": [inventory.default_unit_entry("default", "", 0)],
                "source_inventory": None,
            }
        source_inventory = inventory.scan_play_java(config.play_repo)
        discovered = inventory.discover_migration_units(config.play_repo)
        units = inventory.merge_discovered_migration_units(None, discovered)
        play_surface_inventory = None
        if ctx.inventory_runner is not None:
            play_surface_inventory = ctx.inventory_runner(config)
            _log_play_surface_inventory(play_surface_inventory)
        return {
            "migration_units": units,
            "source_inventory": source_inventory,
            "play_surface_inventory": play_surface_inventory,
        }

    def transform_node(state: MigrationState) -> dict:
        idx = state["current_unit_idx"]
        units = [dict(u) for u in (state.get("migration_units") or [])]
        unit = units[idx]
        unit["status"] = "in_progress"
        n_add, m_err = 0, 0
        if ctx.jar_runner is not None:
            n_add, m_err = ctx.jar_runner(config, str(unit.get("path_prefix", "")))
        unit["files_migrated"] = int(unit.get("files_migrated", 0)) + n_add
        if m_err:
            LOG.warning("migrate-app reported %d errors for slice %s", m_err, unit.get("id"))
        units[idx] = unit
        return {"migration_units": units, "last_transform_added": n_add}

    def slice_finalize_node(state: MigrationState) -> dict:
        idx = state.get("current_unit_idx", 0)
        units = [dict(u) for u in (state.get("migration_units") or [])]
        unit = dict(units[idx]) if idx < len(units) else {}
        label = unit.get("id", state.get("slice_id", ""))
        outcome = state.get("outcome")

        if outcome == "success":
            plausible, reason = inventory.migration_output_plausible(
                dry_run=config.dry_run,
                label=label,
                unit=unit,
                source_inventory=state.get("source_inventory"),
                spring_repo=config.spring_repo,
                n_add=state.get("last_transform_added", 0),
            )
            if plausible:
                unit["status"] = "done"
                unit["failure_reason"] = None
            else:
                unit["status"] = "no_migrated_output"
                unit["failure_reason"] = "no_migrated_output"
                LOG.error("Slice %r: %s", label, reason)
        elif outcome == "timeout":
            unit["status"] = "timeout"
            unit["failure_reason"] = "timeout"
        elif outcome == "looping":
            unit["status"] = "loop_detected"
            unit["failure_reason"] = "loop_detected"
            unit["manual_intervention_note"] = _manual_intervention_note(state)
        elif outcome == "infrastructure_error":
            unit["status"] = "needs_manual_fix"
            unit["failure_reason"] = "infrastructure_error"
        elif outcome == "budget_exhausted":
            unit["status"] = "budget_exhausted"
            unit["failure_reason"] = "budget_exhausted"
        elif outcome == "no_llm":
            unit["status"] = "failed"
            unit["failure_reason"] = "no_llm"
        else:  # "failed" (retries_exhausted) or anything else terminal
            unit["status"] = "failed"
            unit["failure_reason"] = "max_retries"
            unit["manual_intervention_note"] = _manual_intervention_note(state)

        if idx < len(units):
            units[idx] = unit

        updates: dict = {"migration_units": units}
        if outcome in RUN_ABORTING_OUTCOMES:
            # Legacy parity: budget/infra/no_llm are unconditional `return N`
            # inside the slice loop — abort the whole run right here.
            updates["run_outcome"] = outcome
            updates["run_exit_code"] = RUN_OUTCOME_EXIT_CODES.get(
                outcome, OUTCOME_EXIT_CODES.get(outcome, 1)
            )
        else:
            updates["current_unit_idx"] = idx + 1
        return updates

    def verify_node(state: MigrationState) -> dict:
        # Cross-module full-compile verification pass (legacy parity:
        # migration_orchestrator.py :2883-2917) — best-effort, log only.
        final_result = ctx.compiler.compile()
        if final_result.errors:
            fix_result = ctx.fixer.run(final_result.errors)
            if fix_result.fixed_count > 0:
                final_result = ctx.compiler.compile()
        if final_result.errors:
            LOG.warning(
                "Final compile still failing after cross-module deterministic fixes "
                "(manual intervention may be needed)."
            )
        migration_verification = inventory.run_verification(
            state.get("source_inventory"), config.spring_repo
        )
        return {"migration_verification": migration_verification}

    return {
        "inventory_node": inventory_node,
        "transform_node": transform_node,
        "slice_finalize_node": slice_finalize_node,
        "verify_node": verify_node,
    }
