"""config.py's choose_model/TaskSignals: heuristic cheap/premium routing
based on retry_count OR item_count, replacing the old retry-count-only
model_for_retry."""

from agent.config import AgentConfig, TaskSignals


def test_neither_threshold_crossed_stays_cheap(tmp_path):
    cfg = AgentConfig(spring_repo=tmp_path)
    assert cfg.choose_model(TaskSignals(retry_count=0, item_count=0)) == cfg.model_cheap


def test_retry_count_at_threshold_escalates(tmp_path):
    cfg = AgentConfig(spring_repo=tmp_path)
    assert cfg.escalate_after_retries == 2
    assert cfg.choose_model(TaskSignals(retry_count=2, item_count=0)) == cfg.model_premium
    assert cfg.choose_model(TaskSignals(retry_count=1, item_count=0)) == cfg.model_cheap


def test_item_count_at_threshold_escalates(tmp_path):
    cfg = AgentConfig(spring_repo=tmp_path)
    assert cfg.escalate_item_threshold == 5
    assert cfg.choose_model(TaskSignals(retry_count=0, item_count=5)) == cfg.model_premium
    assert cfg.choose_model(TaskSignals(retry_count=0, item_count=4)) == cfg.model_cheap


def test_either_signal_crossing_is_enough(tmp_path):
    cfg = AgentConfig(spring_repo=tmp_path)
    assert cfg.choose_model(TaskSignals(retry_count=2, item_count=0)) == cfg.model_premium
    assert cfg.choose_model(TaskSignals(retry_count=0, item_count=5)) == cfg.model_premium


def test_escalate_item_threshold_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv("MIGRATION_ESCALATE_ITEM_THRESHOLD", "3")
    cfg = AgentConfig(spring_repo=tmp_path)
    assert cfg.escalate_item_threshold == 3
    assert cfg.choose_model(TaskSignals(retry_count=0, item_count=3)) == cfg.model_premium
