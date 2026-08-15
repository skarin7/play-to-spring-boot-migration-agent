"""``migration-status.json`` v2 dual-write: a human-readable derived artifact.

The LangGraph sqlite checkpoint (see checkpoint.py) is the source of truth for
resumability. This module only *projects* ``MigrationState`` into the same v2
JSON shape the legacy orchestrator produces (schema_version 2, migration_units
with error_fingerprints/det_fix_log — see migration_orchestrator.py:925-1069),
so existing tooling/humans reading migration-status.json keep working during
the transition. Never read back to resume a run; that's sqlite's job.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import AgentConfig
from .state import MigrationState


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def state_to_status_v2(state: MigrationState, config: AgentConfig) -> dict[str, Any]:
    """Project MigrationState into the v2 migration-status.json shape."""
    units = state.get("migration_units") or []
    run_outcome = state.get("run_outcome")
    if run_outcome:
        current_step = "done" if run_outcome == "success" else "needs_attention"
    else:
        current_step = "transform_validate"

    return {
        "schema_version": 2,
        "current_step": current_step,
        "source_inventory": state.get("source_inventory"),
        "migration_units": [dict(u) for u in units],
        "migration_verification": state.get("migration_verification"),
        "autonomous": {
            "total_llm_calls": state.get("total_llm_calls", 0),
            "max_total_llm_calls": config.max_total_llm_calls,
            "total_cost_usd": round(state.get("total_cost_usd", 0.0), 6),
            "max_total_cost_usd": config.max_total_cost_usd,
            "prompt_cache_key": None,
        },
        "run_outcome": run_outcome,
        "run_exit_code": state.get("run_exit_code"),
        # M6 Task 11: carried through so report.py's --report-only path can
        # regenerate report.html from migration-status.json alone, without
        # re-running the graph (status_v2_to_state only adopts the narrower
        # set of fields a RESUMED RUN needs -- see that function's own
        # docstring -- report_only() reads these straight from the raw dict).
        "signature_findings": [dict(f) for f in (state.get("signature_findings") or [])],
        "findings": [dict(f) for f in (state.get("findings") or [])],
        "test_result": state.get("test_result"),
        "endpoint_verification": state.get("endpoint_verification"),
    }


def write_status_v2(state: MigrationState, config: AgentConfig) -> Path:
    status = state_to_status_v2(state, config)
    path = config.status_path or (config.spring_repo / "migration-status.json")
    atomic_write_json(path, status)
    return path


def migrate_v1_to_v2(raw: dict[str, Any]) -> dict[str, Any]:
    """Upgrade a v1 (or unmigrated) migration-status.json dict to v2 schema.

    Copied from scripts/migration_orchestrator.py for byte-parity (the live
    definition there, migration_orchestrator.py:1033 -- that file actually
    has two definitions of this name; the second silently wins at import
    time, a pre-existing bug in the legacy script this port deliberately
    does not reproduce), decoupled from the legacy module per the
    tools/*.py convention (toolkit_jar.py, setup_ops.py).

    - Converts errors_history (v1: list of full error dicts) on each
      migration_units entry to error_fingerprints (v2: sorted
      "file:line:msg" string lists, last 5 rounds kept).
    - Adds det_fix_log: [] where absent.
    - Returns raw unchanged (same object) if schema_version is already 2.
    """
    if raw.get("schema_version") == 2:
        return raw

    out = dict(raw)
    out["schema_version"] = 2

    def _convert_unit(u: dict[str, Any]) -> dict[str, Any]:
        u = dict(u)
        history = u.pop("errors_history", None)
        if u.get("error_fingerprints") is None:
            fingerprints: list[list[str]] = []
            for round_errors in history or []:
                if isinstance(round_errors, list):
                    fps = sorted(
                        f"{e.get('file', '')}:{e.get('line', 0)}:{(e.get('message') or '').strip()}"
                        for e in round_errors
                        if isinstance(e, dict)
                    )
                    fingerprints.append(fps)
                elif isinstance(round_errors, str):
                    fingerprints.append([round_errors])
            u["error_fingerprints"] = fingerprints[-5:]
        u.setdefault("det_fix_log", [])
        return u

    raw_mu = out.get("migration_units")
    if isinstance(raw_mu, list):
        out["migration_units"] = [_convert_unit(u) if isinstance(u, dict) else u for u in raw_mu]

    return out


def status_v2_to_state(raw: dict[str, Any]) -> dict[str, Any]:
    """Adopt a legacy migration-status.json into initial MigrationState fields.

    Only consulted when no sqlite checkpoint exists yet for a thread (see
    checkpoint.py / cli.py) -- once a langgraph checkpoint exists, sqlite is
    the sole source of truth and this function is never consulted again for
    that thread ("never read back to resume" in the module docstring above
    is about *our own* dual-write specifically; adoption is a distinct,
    one-time bootstrap from either a legacy-engine run or a prior dual-write
    artifact when no checkpoint history exists at all).

    Narrow by design: only the fields the langgraph engine's own slice
    pipeline actually reads are adopted (migration_units, source_inventory,
    migration_verification, autonomous.total_llm_calls). M4 phase fields
    (routes_attempts, config_mapping_attempts, runtime_wiring_attempts,
    phase, ...) are deliberately left unset -- the legacy engine never had
    those phases, so they run for the first time on the langgraph
    continuation, which is correct, not a gap.

    run_outcome/run_exit_code are deliberately NOT adopted: seeding a
    terminal run_outcome into the graph's initial state would make
    route_after_setup short-circuit straight to run_halt before doing any
    work, defeating the point of adoption.
    """
    migrated = migrate_v1_to_v2(dict(raw))
    state: dict[str, Any] = {}

    units = migrated.get("migration_units")
    if isinstance(units, list):
        cleaned = [dict(u) for u in units if isinstance(u, dict)]
        if cleaned:
            state["migration_units"] = cleaned

    source_inventory = migrated.get("source_inventory")
    if source_inventory is not None:
        state["source_inventory"] = source_inventory

    migration_verification = migrated.get("migration_verification")
    if migration_verification is not None:
        state["migration_verification"] = migration_verification

    total_llm_calls = (migrated.get("autonomous") or {}).get("total_llm_calls")
    if isinstance(total_llm_calls, int):
        state["total_llm_calls"] = total_llm_calls

    total_cost_usd = (migrated.get("autonomous") or {}).get("total_cost_usd")
    if isinstance(total_cost_usd, (int, float)):
        state["total_cost_usd"] = float(total_cost_usd)

    return state
