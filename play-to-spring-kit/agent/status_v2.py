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
            "prompt_cache_key": None,
        },
        "run_outcome": run_outcome,
        "run_exit_code": state.get("run_exit_code"),
    }


def write_status_v2(state: MigrationState, config: AgentConfig) -> Path:
    status = state_to_status_v2(state, config)
    path = config.status_path or (config.spring_repo / "migration-status.json")
    atomic_write_json(path, status)
    return path
