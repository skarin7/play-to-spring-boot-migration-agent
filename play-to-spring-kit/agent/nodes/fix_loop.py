"""Compile-fix subgraph nodes (M1). Phase-agnostic: `state["phase"]` only
changes where done/infra/halt exit to (route_by_phase in nodes/common.py),
never these nodes' own logic — see agent/graph.py's module docstring.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from .. import guards, legacy_logic
from ..agents.compile_fix import run_compile_fix
from ..config import AgentConfig
from ..state import OUTCOME_EXIT_CODES, MigrationState

if TYPE_CHECKING:
    from ..graph import RuntimeCtx

LOG = logging.getLogger("agent.nodes.fix_loop")

MAX_ERRORS_IN_STATE = 200
LOG_TAIL_CHARS = 2000
FINGERPRINT_HISTORY = 5


def route_after_compile(state: MigrationState) -> str:
    # Checked before every other branch: a Play-repo integrity violation
    # aborts the whole run regardless of phase (slice vs fix_cycle), unlike
    # every other compile outcome which stays scoped to route_by_phase.
    if state.get("play_repo_guard_status"):
        return "play_repo_tampered"
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


def route_after_det_fix(state: MigrationState) -> str:
    fingerprints = state.get("error_fingerprints", [])
    stuck = len(fingerprints) >= 2 and legacy_logic.is_looping(
        fingerprints[-1], fingerprints[:-1]
    )
    if len(fingerprints) >= 2:
        # M6 Task 12: same diagnostic classification as guards.py, logged
        # here too since this is the OTHER place is_looping's decision
        # feeds a route -- does not change the stuck/not-stuck decision above.
        reason = legacy_logic.stuck_vs_progress_reason(fingerprints[-1], fingerprints[:-1])
        LOG.info("det_fix loop check: stuck=%s reason=%s", stuck, reason)
    if state.get("det_fixed_last_round", 0) > 0 and not stuck:
        return "compile"
    return "cluster"


def route_after_guard(state: MigrationState) -> str:
    return "agent" if state.get("guard_decision") == "agent" else "halt"


def play_repo_tampered_node(state: MigrationState) -> dict:
    from ..state import RUN_OUTCOME_EXIT_CODES

    return {
        "run_outcome": "play_repo_tampered",
        "run_exit_code": RUN_OUTCOME_EXIT_CODES["play_repo_tampered"],
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


def build(config: AgentConfig, ctx: "RuntimeCtx") -> dict[str, Callable]:
    def compile_node(state: MigrationState) -> dict:
        # Checked unconditionally on every loop iteration (legacy parity:
        # migration_orchestrator.py:2531 checks elapsed time at the top of
        # every while-loop pass, including deterministic-only rounds that
        # never reach guard_node).
        if guards.timed_out(state, config):
            LOG.warning("slice timed out before compile")
            return {"guard_decision": "timeout"}

        # Play-repo integrity guard (M6 Task 2): a file-stat walk, cheap
        # relative to `mvn`, run every compile round so tampering is caught
        # promptly rather than only at run end. "error" halts exactly like
        # "tampered" -- see tools/play_guard.py module docstring. This is a
        # run-aborting condition regardless of phase (slice vs fix_cycle) --
        # route_after_compile sends it straight to "halt" via a dedicated
        # state key rather than overloading guard_decision, which halt_node
        # maps to slice-outcome vocabulary ("timeout"/"looping"/etc.) that
        # would misreport what actually happened.
        if config.play_guard_enabled and config.play_repo is not None:
            from ..tools.play_guard import check as play_guard_check

            baseline_path = config.migration_dir / "play-baseline.json"
            status = play_guard_check(config.play_repo, baseline_path)
            if status != "clean":
                LOG.error("play-repo guard: status=%s -- halting the run", status)
                return {"play_repo_guard_status": status}

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

    def agent_node(state: MigrationState) -> dict:
        edited, result = run_compile_fix(
            config=config,
            cluster_dicts=state.get("last_clusters", []),
            retry_count=state.get("retry_count", 0),
            slice_id=state.get("slice_id", "default"),
            model_override=ctx.model_override,
        )
        updates: dict = {
            "retry_count": state.get("retry_count", 0) + 1,
            "total_llm_calls": state.get("total_llm_calls", 0) + 1,
            "total_cost_usd": state.get("total_cost_usd", 0.0) + (result.total_cost_usd or 0.0),
            "last_edited_files": [str(p) for p in edited],
        }
        if result.manual_review_reason is not None:
            updates["agent_manual_review_reason"] = result.manual_review_reason
        return updates

    return {
        "compile_node": compile_node,
        "det_fix_node": det_fix_node,
        "cluster_node": cluster_node,
        "guard_node": guard_node,
        "agent_node": agent_node,
    }
