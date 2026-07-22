"""status_v2.py dual-write tests: MigrationState -> migration-status.json v2 shape."""

import json

from agent.config import AgentConfig
from agent.status_v2 import state_to_status_v2, write_status_v2


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
