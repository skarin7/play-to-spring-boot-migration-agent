from pathlib import Path

from agent.config import AgentConfig
from agent.guards import decide


def make_config(tmp_path: Path, **kw) -> AgentConfig:
    cfg = AgentConfig(spring_repo=tmp_path, **kw)
    cfg.api_key = "test-key"
    return cfg


BASE_STATE = {
    "retry_count": 0,
    "total_llm_calls": 0,
    "slice_started_at": 1000.0,
    "error_fingerprints": [],
}


def test_agent_allowed(tmp_path):
    cfg = make_config(tmp_path)
    assert decide(dict(BASE_STATE), cfg, now=1010.0) == "agent"


def test_budget_exhausted(tmp_path):
    cfg = make_config(tmp_path)
    cfg.max_total_llm_calls = 3
    state = dict(BASE_STATE, total_llm_calls=3)
    assert decide(state, cfg, now=1010.0) == "budget_exhausted"


def test_retries_exhausted(tmp_path):
    cfg = make_config(tmp_path)
    cfg.max_retries_per_layer = 2
    state = dict(BASE_STATE, retry_count=2)
    assert decide(state, cfg, now=1010.0) == "retries_exhausted"


def test_timeout(tmp_path):
    cfg = make_config(tmp_path)
    cfg.timeout_layer_mins = 1
    state = dict(BASE_STATE)
    assert decide(state, cfg, now=1000.0 + 61) == "timeout"
    assert decide(state, cfg, now=1000.0 + 59) == "agent"


def test_looping_identical_fingerprints(tmp_path):
    cfg = make_config(tmp_path)
    fp = ["A.java:1:cannot find symbol"]
    state = dict(BASE_STATE, error_fingerprints=[fp, fp])
    assert decide(state, cfg, now=1010.0) == "looping"


def test_no_llm_without_api_key(tmp_path):
    cfg = make_config(tmp_path)
    cfg.api_key = None
    assert decide(dict(BASE_STATE), cfg, now=1010.0) == "no_llm"


def test_priority_budget_before_loop(tmp_path):
    cfg = make_config(tmp_path)
    cfg.max_total_llm_calls = 1
    fp = ["A.java:1:x"]
    state = dict(BASE_STATE, total_llm_calls=1, error_fingerprints=[fp, fp])
    assert decide(state, cfg, now=1010.0) == "budget_exhausted"
