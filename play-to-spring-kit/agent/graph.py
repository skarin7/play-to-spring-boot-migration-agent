"""Migration engine as a LangGraph state graph.

Topology (M4 — routes phase wraps the M1 compile-fix loop a second time,
after the M2 slice pipeline finishes, via a generic "fix cycle" re-entry
mechanism any future phase can reuse — see the note below the diagram):

    START -> inventory -+-> slice_router -+-> transform -> compile -+-> slice_finalize
                        |  (no slices)     |  (all done)             |
                        +-> run_halt(6)    +-> routes_node            |
                                                                      |
                                          slice_router <---- slice_finalize (continue)
                                          run_halt <-------- slice_finalize (abort: budget/infra/no_llm)

    routes_node -+-> routes_node        (self-loop: more unmapped routes, budget left)
                 +-> routes_fix_prep -> compile   (re-enter compile-fix subgraph, phase=fix_cycle)
                 +-> config_mapping                (no play_repo / no conf/routes: no-op)
                 +-> run_halt                      (routes_node itself hit the global LLM budget)

    after_fix_cycle -+-> <fix_cycle_return_to>  (phase reset to "slice"; non-blocking unless budget_exhausted)
                      +-> run_halt               (budget_exhausted only)

    config_mapping -+-> config_mapping  (self-loop: leftover config keys, budget left)
                     +-> verify          (no leftover / attempts exhausted / no conf-export to map)
                     +-> run_halt        (config_mapping itself hit the global LLM budget)

    verify -+-> run_done
            +-> run_halt

    compile -+-> done ----------------------> slice_finalize | after_fix_cycle (route_by_phase)
             +-> infra ---------------------> slice_finalize | after_fix_cycle (route_by_phase)
             +-> det_fix -+-> compile          (deterministic re-loop)
                          +-> cluster -> guard -+-> agent -> compile
                                                +-> halt --> slice_finalize | after_fix_cycle (route_by_phase)

Determinism-first: the LLM agent is reached only after the deterministic
fixers made no progress, and only if the guard (budget / retries / timeout /
fingerprint loop detection) allows it. The compile-fix subgraph itself is
phase-agnostic: `state["phase"]` ("slice" default | "fix_cycle") only changes
where done/infra/halt exit to (route_by_phase), never their own logic.

Generic fix-cycle re-entry (any phase, not just routes, that needs to make
edits and then re-verify via the shared compile-fix subgraph): a phase's own
"<phase>_fix_prep" node (e.g. routes_fix_prep) sets phase="fix_cycle" and
fix_cycle_return_to="<node to resume at>", then edges to "compile". When the
subgraph terminates (done/infra/halt), route_by_phase sends it to the single
generic after_fix_cycle node, which aborts the whole run on
outcome=="budget_exhausted" (legacy RUN_ABORTING_OUTCOMES parity) or
otherwise resets phase back to "slice" and hands control to whatever
fix_cycle_return_to says (route_after_fix_cycle) — every possible
fix_cycle_return_to target must be enumerated once in that edge's mapping
dict, but no new node or router is needed per phase.

Per-slice vs run-level state (legacy parity: migration_orchestrator.py's
`for label, le, ... in entity_iterable` loop at :2500 vs. the `while True`
compile-fix loop nested inside it at :2530):
  - migration_units, source_inventory, total_llm_calls,
    excluded_error_signatures persist across the whole run.
  - retry_count, error_fingerprints, det_fix_log, last_compile, last_clusters,
    last_edited_files, slice_started_at, outcome, exit_code are reset by
    slice_router each time it advances to a new unit, and again by any
    "<phase>_fix_prep" node before its fix-cycle re-entry.
  - budget_exhausted / infrastructure_error / no_llm abort the whole run
    immediately; every other per-slice outcome just ends that slice and lets
    slice_router continue to the next unit (legacy: `return N` vs `break`).
    Inside a fix cycle only budget_exhausted aborts the run (after_fix_cycle);
    infra/no_llm there are non-blocking (logged; e.g. routes mapping is
    best-effort and never gates the run — routes were never part of the
    legacy tool's automated scope to begin with). config_mapping is
    similarly best-effort and non-blocking (it only edits .properties
    values, so it never needs a fix-cycle re-entry of its own — it just
    self-loops or gives up and proceeds straight to verify).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph

from . import guards, inventory, legacy_logic
from .agents.bootstrap import run_bootstrap
from .agents.compile_fix import run_compile_fix
from .agents.config_mapping import run_config_mapping_agent
from .agents.routes import run_routes_agent
from .config import AgentConfig
from .state import (
    OUTCOME_EXIT_CODES,
    RUN_ABORTING_OUTCOMES,
    RUN_OUTCOME_EXIT_CODES,
    UNIT_TERMINAL_FAILURE_STATUSES,
    MigrationState,
)
from .status_v2 import atomic_write_json
from .tools.config_mapping import append_properties, diff_config_keys, flatten_play_conf, read_properties_keys
from .tools.routes_parser import diff_routes, find_spring_mappings, parse_routes_file

LOG = logging.getLogger("agent.graph")

MAX_ERRORS_IN_STATE = 200
LOG_TAIL_CHARS = 2000
FINGERPRINT_HISTORY = 5

JarRunner = Callable[[AgentConfig, str], "tuple[int, int]"]


@dataclass
class RuntimeCtx:
    """Process-local components (not checkpointed). Injectable for tests."""

    compiler: Any
    fixer: Any
    clusterer: Any
    model_override: Any = None
    jar_runner: JarRunner | None = None
    setup_ops: Any = None
    bootstrap_model_override: Any = None


def _default_jar_runner(config: AgentConfig, path_prefix: str) -> tuple[int, int]:
    from .tools.toolkit_jar import migrate_until_done

    if config.play_repo is None or config.jar_path is None or not config.jar_path.is_file():
        return 0, 0
    return migrate_until_done(
        config.play_repo,
        config.jar_path,
        config.spring_repo,
        config.migrate_batch_size,
        config.dry_run,
        path_prefix=path_prefix,
    )


def default_ctx(config: AgentConfig) -> RuntimeCtx:
    from compile_error_fixer import CompileErrorFixer
    from error_clusterer import ErrorClusterer
    from incremental_compiler import IncrementalCompiler

    from .tools.setup_ops import SetupOps

    return RuntimeCtx(
        compiler=IncrementalCompiler(config.spring_repo, dry_run=config.dry_run),
        fixer=CompileErrorFixer(config.spring_repo, dry_run=config.dry_run),
        clusterer=ErrorClusterer(),
        jar_runner=_default_jar_runner,
        setup_ops=SetupOps(),
    )


def _bootstrap_files_present(spring_repo: Path) -> tuple[bool, bool, bool]:
    pom_ok = (spring_repo / "pom.xml").is_file()
    java_root = spring_repo / "src" / "main" / "java"
    app_ok = java_root.is_dir() and any(java_root.rglob("Application.java"))
    props_ok = (spring_repo / "src" / "main" / "resources" / "application.properties").is_file()
    return pom_ok, app_ok, props_ok


def _write_route_map(config: AgentConfig, mapped: list[dict], unmapped: list[dict]) -> dict:
    route_map = {"mapped": mapped, "unmapped": unmapped}
    atomic_write_json(config.migration_dir / "route-map.json", route_map)
    return route_map


def _write_config_map(config: AgentConfig, seed_mapped: dict[str, str], leftover: dict[str, str]) -> dict:
    config_map = {"seed_mapped": seed_mapped, "leftover": leftover}
    atomic_write_json(config.migration_dir / "config-map.json", config_map)
    return config_map


def build_graph(config: AgentConfig, ctx: RuntimeCtx | None = None):
    ctx = ctx or default_ctx(config)

    # ------------------------------------------------------------------
    # Setup + bootstrap nodes (M3)
    # ------------------------------------------------------------------

    def setup_node(state: MigrationState) -> dict:
        if ctx.setup_ops is None:
            return {}
        for phase, op in (("jar", ctx.setup_ops.ensure_jar), ("setup.sh", ctx.setup_ops.install)):
            ok, msg = op(config)
            LOG.info("setup(%s): %s", phase, msg)
            if not ok:
                LOG.error("setup(%s) failed: %s", phase, msg)
                return {
                    "run_outcome": "setup_failed",
                    "run_exit_code": RUN_OUTCOME_EXIT_CODES["setup_failed"],
                }
        ok, msg = ctx.setup_ops.export_conf(config)
        LOG.info("setup(export-conf): %s", msg)
        return {}

    def route_after_setup(state: MigrationState) -> str:
        return "halt" if state.get("run_outcome") else "bootstrap_check"

    def bootstrap_check_node(state: MigrationState) -> dict:
        if config.play_repo is None:
            # No Play repo to scaffold from (standalone mode against an already
            # set-up Spring repo) — nothing for the bootstrap agent to do.
            return {"bootstrap_decision": "skip", "bootstrap_attempts": state.get("bootstrap_attempts", 0)}
        pom_ok, app_ok, props_ok = _bootstrap_files_present(config.spring_repo)
        decision = "skip" if (pom_ok and app_ok and props_ok) else "agent"
        return {"bootstrap_decision": decision, "bootstrap_attempts": state.get("bootstrap_attempts", 0)}

    def route_after_bootstrap_check(state: MigrationState) -> str:
        return "inventory" if state.get("bootstrap_decision") == "skip" else "bootstrap_agent"

    def bootstrap_agent_node(state: MigrationState) -> dict:
        attempt = state.get("bootstrap_attempts", 0) + 1
        run_bootstrap(config, attempt, model_override=ctx.bootstrap_model_override)
        return {"bootstrap_attempts": attempt}

    def bootstrap_verify_node(state: MigrationState) -> dict:
        pom_ok, app_ok, props_ok = _bootstrap_files_present(config.spring_repo)
        if pom_ok and app_ok and props_ok:
            return {"bootstrap_decision": "skip"}
        if state.get("bootstrap_attempts", 0) >= config.max_bootstrap_attempts:
            LOG.error(
                "bootstrap: giving up after %d attempts (pom=%s app=%s props=%s)",
                state.get("bootstrap_attempts", 0), pom_ok, app_ok, props_ok,
            )
            return {
                "bootstrap_decision": "exhausted",
                "run_outcome": "init_failed",
                "run_exit_code": RUN_OUTCOME_EXIT_CODES["init_failed"],
            }
        return {"bootstrap_decision": "retry"}

    def route_after_bootstrap_verify(state: MigrationState) -> str:
        return {"skip": "inventory", "retry": "bootstrap_agent", "exhausted": "run_halt"}[
            state.get("bootstrap_decision", "exhausted")
        ]

    # ------------------------------------------------------------------
    # Slice-pipeline nodes (M2)
    # ------------------------------------------------------------------

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
        return {"migration_units": units, "source_inventory": source_inventory}

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
            "slice_started_at": 0.0,
            "guard_decision": None,
            "outcome": None,
            "exit_code": None,
        }

    def route_after_slice_router(state: MigrationState) -> str:
        units = state.get("migration_units") or []
        idx = state.get("current_unit_idx", 0)
        return "transform" if idx < len(units) else "routes"

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

    def route_after_slice_finalize(state: MigrationState) -> str:
        return "halt" if state.get("run_outcome") else "router"

    # ------------------------------------------------------------------
    # Routes phase (M4) — runs once after the slice pipeline finishes,
    # before the cross-module verify pass.
    # ------------------------------------------------------------------

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

        # Budget check mirrors guards.decide()'s ordering exactly (checked
        # before per-slice retries there, before the per-phase attempt cap
        # here): the global LLM budget is a hard stop for the whole run, not
        # just this phase, so it must be checked before every agent call —
        # not only via the routes-local max_routes_attempts cap.
        if state.get("total_llm_calls", 0) >= config.max_total_llm_calls:
            LOG.warning("routes: global LLM budget exhausted, aborting run")
            route_map = _write_route_map(config, mapped, unmapped)
            return {
                "route_map": route_map,
                "run_outcome": "budget_exhausted",
                "run_exit_code": RUN_OUTCOME_EXIT_CODES["budget_exhausted"],
            }

        if attempts >= config.max_routes_attempts:
            LOG.warning("routes: giving up after %d attempts, %d routes still unmapped", attempts, len(unmapped))
            route_map = _write_route_map(config, mapped, unmapped)
            return {"route_map": route_map, "routes_decision": "proceed"}

        run_routes_agent(config, unmapped, attempt=attempts + 1, model_override=ctx.model_override)
        return {
            "routes_attempts": attempts + 1,
            "total_llm_calls": state.get("total_llm_calls", 0) + 1,
            "routes_decision": "loop",
        }

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

    # ------------------------------------------------------------------
    # Config-mapping phase (M4) — runs after the routes phase, before the
    # cross-module verify pass. Editing .properties values can never break
    # `mvn compile`, so unlike routes this phase never needs to re-enter the
    # compile-fix subgraph: it only self-loops or hands off to verify.
    # ------------------------------------------------------------------

    def config_mapping_node(state: MigrationState) -> dict:
        attempts = state.get("config_mapping_attempts", 0)
        properties_path = config.spring_repo / "src" / "main" / "resources" / "application.properties"
        conf_path = (config.play_repo / "conf" / "application.conf") if config.play_repo is not None else None

        if attempts == 0:
            if (
                config.play_repo is None
                or not config.export_play_conf
                or conf_path is None
                or not conf_path.is_file()
                or not properties_path.is_file()
            ):
                # Nothing to map (no Play repo, conf export disabled, no
                # conf/application.conf, or the export never produced a
                # properties file to begin with) — no-op.
                config_map = _write_config_map(config, {}, {})
                return {"config_map": config_map, "config_mapping_decision": "noop"}

        # Recompute fresh every round: a previous agent round may have
        # resolved some (or all) of the previously-leftover keys, and the
        # seed pass below may append new canonical keys this round too.
        flattened = flatten_play_conf(conf_path)
        existing_keys = read_properties_keys(properties_path)
        seed_mapped, leftover = diff_config_keys(flattened, existing_keys)

        # Deterministic pass first, every round, regardless of attempts/budget
        # (zero LLM cost — same "determinism first" principle used throughout
        # this codebase; never gate this behind the agent or the attempts cap).
        if seed_mapped:
            append_properties(properties_path, seed_mapped)

        # Cumulative seed_mapped across rounds: each round's diff only
        # reflects keys not yet appended, so merge with what earlier rounds
        # already wrote to keep config-map.json a complete record. `leftover`
        # is always just this round's fresh diff (an agent round may have
        # resolved some of it already).
        cumulative_seed_mapped = dict((state.get("config_map") or {}).get("seed_mapped") or {})
        cumulative_seed_mapped.update(seed_mapped)

        if not leftover:
            config_map = _write_config_map(config, cumulative_seed_mapped, {})
            return {"config_map": config_map, "config_mapping_decision": "proceed"}

        # Budget check mirrors routes_node's ordering exactly: checked before
        # the per-phase attempt cap and before invoking the agent, since the
        # global LLM budget is a hard stop for the whole run, not just this
        # phase.
        if state.get("total_llm_calls", 0) >= config.max_total_llm_calls:
            LOG.warning("config_mapping: global LLM budget exhausted, aborting run")
            config_map = _write_config_map(config, cumulative_seed_mapped, leftover)
            return {
                "config_map": config_map,
                "run_outcome": "budget_exhausted",
                "run_exit_code": RUN_OUTCOME_EXIT_CODES["budget_exhausted"],
            }

        if attempts >= config.max_config_mapping_attempts:
            LOG.warning(
                "config_mapping: giving up after %d attempts, %d keys still unmapped", attempts, len(leftover)
            )
            config_map = _write_config_map(config, cumulative_seed_mapped, leftover)
            return {"config_map": config_map, "config_mapping_decision": "proceed"}

        run_config_mapping_agent(config, leftover, attempt=attempts + 1, model_override=ctx.model_override)
        return {
            "config_mapping_attempts": attempts + 1,
            "total_llm_calls": state.get("total_llm_calls", 0) + 1,
            "config_mapping_decision": "loop",
        }

    def route_after_config_mapping_node(state: MigrationState) -> str:
        if state.get("run_outcome"):
            return "halt"
        decision = state.get("config_mapping_decision")
        if decision == "loop":
            return "config_mapping"
        # Both "proceed" and "noop" land on verify — there's no routes-style
        # noop/proceed distinction needed downstream since config_mapping has
        # no fix-cycle to skip.
        return "verify"

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

    def route_after_verify(state: MigrationState) -> str:
        units = state.get("migration_units") or []
        failed = any(u.get("status") in UNIT_TERMINAL_FAILURE_STATUSES for u in units)
        return "halt" if failed else "done"

    def run_done_node(state: MigrationState) -> dict:
        return {"run_outcome": "success", "run_exit_code": RUN_OUTCOME_EXIT_CODES["success"]}

    def run_halt_node(state: MigrationState) -> dict:
        if state.get("run_outcome"):
            return {}
        units = state.get("migration_units") or []
        outcome = "no_slices" if not units else "slice_failures"
        return {"run_outcome": outcome, "run_exit_code": RUN_OUTCOME_EXIT_CODES[outcome]}

    # ------------------------------------------------------------------
    # Compile-fix subgraph nodes (M1)
    # ------------------------------------------------------------------

    def compile_node(state: MigrationState) -> dict:
        # Checked unconditionally on every loop iteration (legacy parity:
        # migration_orchestrator.py:2531 checks elapsed time at the top of
        # every while-loop pass, including deterministic-only rounds that
        # never reach guard_node).
        if guards.timed_out(state, config):
            LOG.warning("slice timed out before compile")
            return {"guard_decision": "timeout"}

        changed = [Path(p) for p in state.get("last_edited_files", [])]
        result = ctx.compiler.compile(changed or None)
        errors = list(result.errors)

        if errors:
            # Revert deterministic fixes that produced parse errors (legacy parity).
            reverted = ctx.fixer.revert_bad_fixes(errors)
            if reverted:
                LOG.warning("reverted %d bad deterministic fixes", len(reverted))
        else:
            ctx.fixer.delete_bak_files()

        updates: dict = {
            "last_compile": {
                "returncode": result.returncode,
                "error_count": len(errors),
                "infra": bool(result.is_infrastructure_error),
                "log_tail": (result.log or "")[-LOG_TAIL_CHARS:],
                "errors": errors[:MAX_ERRORS_IN_STATE],
            },
            "last_edited_files": [],
        }
        if errors:
            fingerprints = list(state.get("error_fingerprints", []))
            fingerprints.append(legacy_logic.normalize_errors(errors))
            updates["error_fingerprints"] = fingerprints[-FINGERPRINT_HISTORY:]
        if not state.get("slice_started_at"):
            updates["slice_started_at"] = time.time()
        LOG.info("compile: rc=%s errors=%d infra=%s", result.returncode, len(errors), result.is_infrastructure_error)
        return updates

    def route_after_compile(state: MigrationState) -> str:
        if state.get("guard_decision") == "timeout":
            return "halt"
        summary = state.get("last_compile", {})
        # Infra check first: JDK/Lombok crashes produce no parsed errors,
        # so an error_count==0 check alone would falsely report success.
        if summary.get("infra"):
            return "infra"
        if summary.get("error_count", 0) == 0:
            return "done"
        return "det_fix"

    def det_fix_node(state: MigrationState) -> dict:
        errors = state.get("last_compile", {}).get("errors", [])
        _infra, dep, code = legacy_logic.classify_compile_errors(errors)

        pom_added = legacy_logic.try_deterministic_pom_fix(config.spring_repo, dep, config.dry_run)
        fix_result = ctx.fixer.run(code)

        log = list(state.get("det_fix_log", []))
        if pom_added:
            log.append(f"pom: added {pom_added} dependencies")
        log.extend(fix_result.det_fix_log)

        det_fixed = pom_added + fix_result.fixed_count
        LOG.info("det_fix: pom=%d code=%d unresolved=%d", pom_added, fix_result.fixed_count, len(fix_result.unresolved))

        updates: dict = {"det_fixed_last_round": det_fixed, "det_fix_log": log[-100:]}
        # A pom.xml change affects the whole classpath, so force a full
        # recompile (legacy parity: migration_orchestrator.py:2643-2646 never
        # sets last_edited_files on the pom-fix path). Otherwise scope the
        # next compile to the files the fixer actually touched (legacy parity:
        # :2654-2655 reads _fixer._backed_up.keys() for IncrementalCompiler).
        if pom_added == 0:
            backed_up = getattr(ctx.fixer, "_backed_up", None) or {}
            if backed_up:
                updates["last_edited_files"] = [str(p) for p in backed_up.keys()]
        return updates

    def route_after_det_fix(state: MigrationState) -> str:
        fingerprints = state.get("error_fingerprints", [])
        stuck = len(fingerprints) >= 2 and legacy_logic.is_looping(
            fingerprints[-1], fingerprints[:-1]
        )
        if state.get("det_fixed_last_round", 0) > 0 and not stuck:
            return "compile"
        return "cluster"

    def cluster_node(state: MigrationState) -> dict:
        errors = state.get("last_compile", {}).get("errors", [])
        excluded = set(state.get("excluded_error_signatures", []))
        remaining = [e for e in errors if legacy_logic.error_signature(e) not in excluded]
        clusters = ctx.clusterer.cluster(remaining)
        cluster_dicts = [
            {
                "root_cause": c.root_cause,
                "representative": c.representative,
                "affected_files": c.affected_files,
                "count": c.count,
                "suggested_fix": c.suggested_fix,
            }
            for c in clusters
        ]
        return {"last_clusters": cluster_dicts}

    def guard_node(state: MigrationState) -> dict:
        decision = guards.decide(state, config)
        updates: dict = {"guard_decision": decision}
        if decision == "looping":
            # Exclude the repeating signatures so a future run can make progress
            # on other errors (legacy parity).
            fingerprints = state.get("error_fingerprints", [])
            excluded = set(state.get("excluded_error_signatures", []))
            if fingerprints:
                excluded.update(fingerprints[-1])
            updates["excluded_error_signatures"] = sorted(excluded)
        LOG.info("guard: %s", decision)
        return updates

    def route_after_guard(state: MigrationState) -> str:
        return "agent" if state.get("guard_decision") == "agent" else "halt"

    def agent_node(state: MigrationState) -> dict:
        edited, _result = run_compile_fix(
            config=config,
            cluster_dicts=state.get("last_clusters", []),
            retry_count=state.get("retry_count", 0),
            slice_id=state.get("slice_id", "default"),
            model_override=ctx.model_override,
        )
        return {
            "retry_count": state.get("retry_count", 0) + 1,
            "total_llm_calls": state.get("total_llm_calls", 0) + 1,
            "last_edited_files": [str(p) for p in edited],
        }

    def done_node(state: MigrationState) -> dict:
        return {"outcome": "success", "exit_code": OUTCOME_EXIT_CODES["success"]}

    def infra_node(state: MigrationState) -> dict:
        return {
            "outcome": "infrastructure_error",
            "exit_code": OUTCOME_EXIT_CODES["infrastructure_error"],
        }

    def halt_node(state: MigrationState) -> dict:
        decision = state.get("guard_decision", "retries_exhausted")
        outcome = {
            "budget_exhausted": "budget_exhausted",
            "retries_exhausted": "failed",
            "timeout": "timeout",
            "looping": "looping",
            "no_llm": "no_llm",
        }.get(decision, "failed")
        return {"outcome": outcome, "exit_code": OUTCOME_EXIT_CODES[outcome]}

    def route_by_phase(state: MigrationState) -> str:
        # done/infra/halt exit the compile-fix subgraph to different places
        # depending on which pipeline re-entered it (M2 slice loop vs any
        # phase's fix-cycle re-entry) — the nodes themselves are unchanged.
        # Always a plain two-way check regardless of how many phases exist:
        # after_fix_cycle is the single generic landing node for all of them
        # (see route_after_fix_cycle for the per-phase return-to routing).
        return "slice_finalize" if state.get("phase", "slice") == "slice" else "after_fix_cycle"

    # ------------------------------------------------------------------
    # Wiring
    # ------------------------------------------------------------------

    g = StateGraph(MigrationState)
    g.add_node("setup", setup_node)
    g.add_node("bootstrap_check", bootstrap_check_node)
    g.add_node("bootstrap_agent", bootstrap_agent_node)
    g.add_node("bootstrap_verify", bootstrap_verify_node)

    g.add_node("inventory", inventory_node)
    g.add_node("slice_router", slice_router_node)
    g.add_node("transform", transform_node)
    g.add_node("slice_finalize", slice_finalize_node)
    g.add_node("routes", routes_node)
    g.add_node("routes_fix_prep", routes_fix_prep_node)
    g.add_node("after_fix_cycle", after_fix_cycle_node)
    g.add_node("config_mapping", config_mapping_node)
    g.add_node("verify", verify_node)
    g.add_node("run_done", run_done_node)
    g.add_node("run_halt", run_halt_node)

    g.add_node("compile", compile_node)
    g.add_node("det_fix", det_fix_node)
    g.add_node("cluster", cluster_node)
    g.add_node("guard", guard_node)
    g.add_node("agent", agent_node)
    g.add_node("done", done_node)
    g.add_node("infra", infra_node)
    g.add_node("halt", halt_node)

    g.add_edge(START, "setup")
    g.add_conditional_edges("setup", route_after_setup, {"bootstrap_check": "bootstrap_check", "halt": "run_halt"})
    g.add_conditional_edges(
        "bootstrap_check", route_after_bootstrap_check, {"inventory": "inventory", "bootstrap_agent": "bootstrap_agent"}
    )
    g.add_edge("bootstrap_agent", "bootstrap_verify")
    g.add_conditional_edges(
        "bootstrap_verify",
        route_after_bootstrap_verify,
        {"inventory": "inventory", "bootstrap_agent": "bootstrap_agent", "run_halt": "run_halt"},
    )

    g.add_conditional_edges("inventory", route_after_inventory, {"router": "slice_router", "no_slices": "run_halt"})
    g.add_conditional_edges("slice_router", route_after_slice_router, {"transform": "transform", "routes": "routes"})
    g.add_edge("transform", "compile")

    g.add_conditional_edges(
        "compile",
        route_after_compile,
        {"done": "done", "infra": "infra", "det_fix": "det_fix", "halt": "halt"},
    )
    g.add_conditional_edges(
        "det_fix", route_after_det_fix, {"compile": "compile", "cluster": "cluster"}
    )
    g.add_edge("cluster", "guard")
    g.add_conditional_edges("guard", route_after_guard, {"agent": "agent", "halt": "halt"})
    g.add_edge("agent", "compile")
    g.add_conditional_edges(
        "done", route_by_phase, {"slice_finalize": "slice_finalize", "after_fix_cycle": "after_fix_cycle"}
    )
    g.add_conditional_edges(
        "infra", route_by_phase, {"slice_finalize": "slice_finalize", "after_fix_cycle": "after_fix_cycle"}
    )
    g.add_conditional_edges(
        "halt", route_by_phase, {"slice_finalize": "slice_finalize", "after_fix_cycle": "after_fix_cycle"}
    )
    g.add_conditional_edges(
        "slice_finalize", route_after_slice_finalize, {"router": "slice_router", "halt": "run_halt"}
    )

    g.add_conditional_edges(
        "routes",
        route_after_routes_node,
        {
            "routes": "routes",
            "routes_fix_prep": "routes_fix_prep",
            "config_mapping": "config_mapping",
            "halt": "run_halt",
        },
    )
    g.add_edge("routes_fix_prep", "compile")
    # Every phase's fix_cycle_return_to value must be enumerated here (one
    # line per future phase, in this one place) — LangGraph needs the full
    # destination set upfront.
    g.add_conditional_edges(
        "after_fix_cycle", route_after_fix_cycle, {"halt": "run_halt", "config_mapping": "config_mapping"}
    )
    g.add_conditional_edges(
        "config_mapping",
        route_after_config_mapping_node,
        {"config_mapping": "config_mapping", "verify": "verify", "halt": "run_halt"},
    )

    g.add_conditional_edges("verify", route_after_verify, {"done": "run_done", "halt": "run_halt"})
    g.add_edge("run_done", END)
    g.add_edge("run_halt", END)
    return g


def recursion_limit(config: AgentConfig) -> int:
    # Worst case per LLM round: compile + det_fix + cluster + guard + agent = 5 nodes,
    # plus deterministic re-loops, plus a flat overhead for slice-pipeline
    # bookkeeping nodes (inventory/router/transform/finalize/verify — a handful
    # per slice, negligible next to the LLM-round budget). Generous multiple of
    # the LLM budget, capped.
    return min(150 + config.max_total_llm_calls * 8, 1000)
