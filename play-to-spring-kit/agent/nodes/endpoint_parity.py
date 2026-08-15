"""T5 endpoint parity phase (M6 Task 10): does the migrated app return the
same thing the Play app did -- the one thing T1 (compile), T2 (signatures),
and T3 (routes) all fail to prove.

Runs after boot_run confirms the Spring app started, before run_done.
Deterministic diff logic lives in tools/endpoint_diff.py; this node is thin
glue between that and state.

Dual-boot orchestration (booting Play AND Spring at once, capturing both,
tearing both down) is injected via ctx.endpoint_parity_runner rather than
implemented inline here -- same seam shape as ctx.signature_runner (Task 1),
which solved the identical problem for the `signature` JAR subcommand: a
subprocess this codebase has no existing wrapper for. Play's own boot
mechanism (sbt run / a fat jar, depending on the target project) is
genuinely repo-dependent in a way `tools/maven.py`'s Spring-specific
Popen-watching isn't, so it stays a pluggable seam rather than a forced
implementation -- see docs/superpowers/plans/
2026-08-15-plugin-parity-hardening.md, Task 10's open question (Q2).

A missing runner (no play_repo, no route_map, or the seam simply not wired)
degrades to "not attempted" -- logged, never a run-blocking condition, same
as every other T-tier this engine runs (T2/T4 are equally soft).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable

from ..config import AgentConfig
from ..state import MigrationState

if TYPE_CHECKING:
    from ..graph import RuntimeCtx

LOG = logging.getLogger("agent.nodes.endpoint_parity")


def _findings_from_diff(diff_entries: list[dict], *, unproved: list[dict]) -> list[dict]:
    findings: list[dict] = []
    for entry in diff_entries:
        findings.append({"tier": "T5", "severity": "major", "category": "endpoint_diff", "scope": "final", **entry})
    for entry in unproved:
        findings.append(
            {
                "tier": "T5",
                "severity": "minor",
                "category": "endpoint_unproved",
                "scope": "final",
                "route": f"{entry.get('method')} {entry.get('path')}",
                "reason": entry.get("reason", ""),
            }
        )
    return findings


def build(config: AgentConfig, ctx: "RuntimeCtx") -> dict[str, Callable]:
    def endpoint_parity_node(state: MigrationState) -> dict:
        if ctx.endpoint_parity_runner is None or config.play_repo is None:
            LOG.info("T5 endpoint parity: not attempted (no runner configured or no play_repo)")
            return {"endpoint_verification": {"status": "not_attempted"}}

        route_map = state.get("route_map") or {}
        routes = list(route_map.get("mapped") or []) + list(route_map.get("unmapped") or [])
        if not routes:
            LOG.info("T5 endpoint parity: no routes to probe")
            return {"endpoint_verification": {"status": "not_attempted", "reason": "no routes"}}

        result = ctx.endpoint_parity_runner(config, routes)
        if result is None:
            LOG.warning("T5 endpoint parity: runner returned no result (boot failure or timeout)")
            return {"endpoint_verification": {"status": "error"}}

        diff_entries, unproved, probes_compared = result
        new_findings = _findings_from_diff(diff_entries, unproved=unproved)
        if new_findings:
            LOG.warning(
                "T5 endpoint parity: %d diff finding(s), %d unproved route(s)",
                len(diff_entries), len(unproved),
            )
        existing = list(state.get("findings") or [])

        status = "passed" if not diff_entries else "differences_found"
        return {
            "findings": existing + new_findings,
            "endpoint_verification": {
                "status": status,
                "probes_compared": probes_compared,
                "differences": len(diff_entries),
                "not_captured_after": len(unproved),
            },
        }

    return {"endpoint_parity_node": endpoint_parity_node}
