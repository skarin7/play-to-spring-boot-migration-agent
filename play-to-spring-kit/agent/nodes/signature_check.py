"""T2 signature-preservation tier (M6): a graph node wrapping
``tools/signature_diff.py``. Deterministic, no LLM -- runs after
``slice_finalize`` for the slice just finished, and again unscoped in
``verify_node`` for the whole tree.

A T2 finding never halts the run on its own (see state.py's
signature_findings docstring) -- it's recorded the same way routes/
config_mapping findings are non-blocking today. Wiring a blocker-halts-run
policy is Task 11's job (severity model + report), not this node's.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable

from ..config import AgentConfig
from ..state import MigrationState

if TYPE_CHECKING:
    from ..graph import RuntimeCtx

LOG = logging.getLogger("agent.nodes.signature_check")

# Play's Java source root, relative to play_repo -- same root `signature` must
# scan for path keys to line up with the app/ relative path_prefix values
# inventory.py already produces (see inventory.py:discover_migration_units).
PLAY_JAVA_ROOT = "app"
SPRING_JAVA_ROOT = "src/main/java"


def _run_signature_diff(
    config: AgentConfig,
    ctx: "RuntimeCtx",
    *,
    layer_prefix: str | None,
) -> dict[str, list[dict]] | None:
    """None when there's no Play repo/JAR to compare against -- no signal,
    not a failure, matching every other pre-flight/optional check in this
    codebase (run_inventory_scan, etc.)."""
    if config.play_repo is None or config.jar_path is None or not config.jar_path.is_file():
        return None
    if ctx.signature_runner is None:
        return None

    play_root = config.play_repo / PLAY_JAVA_ROOT
    spring_root = config.spring_repo / SPRING_JAVA_ROOT
    if not play_root.is_dir() or not spring_root.is_dir():
        return None

    play_report, spring_report = ctx.signature_runner(config, play_root, spring_root)
    if play_report is None or spring_report is None:
        return None

    from ..tools.signature_diff import diff_signatures

    return diff_signatures(play_report, spring_report, layer_prefix=layer_prefix)


def _findings_from_diff(diff: dict[str, list[dict]], *, scope: str) -> list[dict]:
    findings: list[dict] = []
    for entry in diff.get("method_missing", []):
        findings.append({"tier": "T2", "severity": "blocker", "category": "method_missing", "scope": scope, **entry})
    for entry in diff.get("signature_changed", []):
        findings.append({"tier": "T2", "severity": "major", "category": "signature_changed", "scope": scope, **entry})
    for entry in diff.get("parse_errors", []):
        findings.append({"tier": "T2", "severity": "minor", "category": "parse_error", "scope": scope, **entry})
    # classes_absent_from_spring is deliberately never turned into a finding --
    # see tools/signature_diff.py:diff_signatures docstring.
    return findings


def build(config: AgentConfig, ctx: "RuntimeCtx") -> dict[str, Callable]:
    def signature_check_node(state: MigrationState) -> dict:
        idx = state.get("current_unit_idx", 0)
        units = state.get("migration_units") or []
        unit = units[idx] if idx < len(units) else {}
        layer_prefix = unit.get("path_prefix") or None

        diff = _run_signature_diff(config, ctx, layer_prefix=layer_prefix)
        if diff is None:
            return {}

        new_findings = _findings_from_diff(diff, scope=unit.get("id", "slice"))
        if new_findings:
            LOG.warning(
                "T2 signature check (%s): %d method_missing, %d signature_changed, %d parse_errors",
                unit.get("id", "slice"),
                len(diff.get("method_missing", [])),
                len(diff.get("signature_changed", [])),
                len(diff.get("parse_errors", [])),
            )
        existing = list(state.get("signature_findings") or [])
        return {"signature_findings": existing + new_findings}

    def signature_check_final_node(state: MigrationState) -> dict:
        diff = _run_signature_diff(config, ctx, layer_prefix=None)
        if diff is None:
            return {}
        new_findings = _findings_from_diff(diff, scope="final")
        if new_findings:
            LOG.warning(
                "T2 final signature check: %d method_missing, %d signature_changed, %d parse_errors",
                len(diff.get("method_missing", [])),
                len(diff.get("signature_changed", [])),
                len(diff.get("parse_errors", [])),
            )
        existing = list(state.get("signature_findings") or [])
        return {"signature_findings": existing + new_findings}

    return {
        "signature_check_node": signature_check_node,
        "signature_check_final_node": signature_check_final_node,
    }
