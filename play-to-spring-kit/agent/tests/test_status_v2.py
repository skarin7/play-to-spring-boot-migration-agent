"""status_v2.py dual-write tests: MigrationState -> migration-status.json v2 shape.

Also covers the reverse direction (M5): adopting a legacy migration-status.json
into initial MigrationState fields when no sqlite checkpoint exists yet.
"""

import json

from agent.config import AgentConfig
from agent.status_v2 import migrate_v1_to_v2, state_to_status_v2, status_v2_to_state, write_status_v2


def test_state_to_status_v2_shape(tmp_path):
    cfg = AgentConfig(spring_repo=tmp_path)
    state = {
        "migration_units": [{"id": "a", "path_prefix": "a", "status": "done"}],
        "source_inventory": {"total_java_files": 5},
        "total_llm_calls": 3,
        "run_outcome": "success",
        "run_exit_code": 0,
    }
    status = state_to_status_v2(state, cfg)
    assert status["schema_version"] == 2
    assert status["current_step"] == "done"
    assert status["migration_units"] == [{"id": "a", "path_prefix": "a", "status": "done"}]
    assert status["autonomous"]["total_llm_calls"] == 3
    assert status["autonomous"]["max_total_llm_calls"] == cfg.max_total_llm_calls
    assert status["run_outcome"] == "success"


def test_state_to_status_v2_needs_attention_when_not_success(tmp_path):
    cfg = AgentConfig(spring_repo=tmp_path)
    status = state_to_status_v2({"run_outcome": "slice_failures"}, cfg)
    assert status["current_step"] == "needs_attention"


def test_state_to_status_v2_in_progress_when_no_run_outcome_yet(tmp_path):
    cfg = AgentConfig(spring_repo=tmp_path)
    status = state_to_status_v2({}, cfg)
    assert status["current_step"] == "transform_validate"


def test_write_status_v2_is_valid_json_on_disk(tmp_path):
    cfg = AgentConfig(spring_repo=tmp_path)
    state = {"migration_units": [], "run_outcome": "success", "run_exit_code": 0}
    path = write_status_v2(state, cfg)
    assert path == tmp_path / "migration-status.json"
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["schema_version"] == 2
    assert on_disk["run_exit_code"] == 0


def test_migrate_v1_to_v2_converts_errors_history_to_fingerprints():
    raw = {
        "migration_units": [
            {
                "id": "a",
                "status": "in_progress",
                "errors_history": [
                    [{"file": "B.java", "line": 2, "message": " dup "}, {"file": "A.java", "line": 1, "message": "x"}],
                    "already-a-fingerprint",
                ],
            }
        ]
    }
    out = migrate_v1_to_v2(raw)
    assert out["schema_version"] == 2
    unit = out["migration_units"][0]
    assert "errors_history" not in unit
    assert unit["error_fingerprints"] == [
        ["A.java:1:x", "B.java:2:dup"],  # sorted
        ["already-a-fingerprint"],
    ]
    assert unit["det_fix_log"] == []


def test_migrate_v1_to_v2_keeps_last_5_rounds_and_is_idempotent():
    raw = {
        "migration_units": [
            {"id": "a", "errors_history": [[{"file": f"F{i}.java", "line": i, "message": "m"}] for i in range(8)]}
        ]
    }
    out = migrate_v1_to_v2(raw)
    assert len(out["migration_units"][0]["error_fingerprints"]) == 5
    # Already v2: returned unchanged (identity, not just equal).
    already_v2 = {"schema_version": 2, "migration_units": [{"id": "a"}]}
    assert migrate_v1_to_v2(already_v2) is already_v2


def test_migrate_v1_to_v2_leaves_already_converted_units_alone():
    raw = {
        "migration_units": [
            {"id": "a", "error_fingerprints": [["kept:as:is"]], "errors_history": [[{"file": "X", "line": 1}]]}
        ]
    }
    out = migrate_v1_to_v2(raw)
    assert out["migration_units"][0]["error_fingerprints"] == [["kept:as:is"]]


def test_status_v2_to_state_adopts_units_inventory_and_llm_calls():
    raw = {
        "schema_version": 2,
        "current_step": "transform_validate",
        "source_inventory": {"total_java_files": 12},
        "migration_units": [
            {"id": "a", "path_prefix": "a", "status": "done"},
            {"id": "b", "path_prefix": "b", "status": "pending"},
        ],
        "migration_verification": {"ok": True},
        "autonomous": {"total_llm_calls": 7, "max_total_llm_calls": 50},
        "run_outcome": "slice_failures",
        "run_exit_code": 5,
    }
    state = status_v2_to_state(raw)
    assert state["migration_units"] == raw["migration_units"]
    assert state["source_inventory"] == {"total_java_files": 12}
    assert state["migration_verification"] == {"ok": True}
    assert state["total_llm_calls"] == 7
    # Deliberately NOT adopted: seeding a terminal run_outcome into initial
    # state would make route_after_setup short-circuit straight to run_halt
    # before doing any work.
    assert "run_outcome" not in state
    assert "run_exit_code" not in state
    # M4 phase fields are deliberately left unset -- the legacy engine never
    # had these phases, so they run for the first time on adoption.
    assert "routes_attempts" not in state
    assert "phase" not in state


def test_status_v2_to_state_converts_v1_input_first():
    raw = {
        "migration_units": [
            {"id": "a", "errors_history": [[{"file": "A.java", "line": 1, "message": "x"}]]},
        ],
        "autonomous": {"total_llm_calls": 2},
    }
    state = status_v2_to_state(raw)
    assert state["migration_units"][0]["error_fingerprints"] == [["A.java:1:x"]]
    assert "errors_history" not in state["migration_units"][0]


def test_status_v2_to_state_handles_missing_fields_gracefully():
    assert status_v2_to_state({}) == {}
    assert status_v2_to_state({"migration_units": "not-a-list"}) == {}
