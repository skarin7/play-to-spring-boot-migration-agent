from pathlib import Path

from agent.config import AgentConfig
from agent.guards import decide


def make_config(tmp_path: Path, **kw) -> AgentConfig:
    cfg = AgentConfig(spring_repo=tmp_path, **kw)
    cfg.api_key = "test-key"
    return cfg


_A_CLUSTER = [{"root_cause": "cannot find symbol", "representative": {}, "affected_files": ["A.java"], "count": 1}]

# last_clusters mirrors what cluster_node always sets before guard_node runs
# in the real graph (route_after_compile only reaches cluster/guard on a
# failed compile, so there's always at least one error to cluster) --
# non-empty here so these tests exercise every OTHER guard branch without
# tripping the "nothing left to fix" check (see test_looping_no_clusters_left
# for that branch specifically).
BASE_STATE = {
    "retry_count": 0,
    "total_llm_calls": 0,
    "slice_started_at": 1000.0,
    "error_fingerprints": [],
    "last_clusters": _A_CLUSTER,
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


def test_looping_no_clusters_left(tmp_path):
    """Regression: every remaining error's signature already excluded (from a
    prior 'looping' round) must halt instead of invoking the agent with an
    empty prompt -- see agent/guards.py's last_clusters check."""
    cfg = make_config(tmp_path)
    state = dict(BASE_STATE, last_clusters=[])
    assert decide(state, cfg, now=1010.0) == "looping"


def test_priority_budget_before_loop(tmp_path):
    cfg = make_config(tmp_path)
    cfg.max_total_llm_calls = 1
    fp = ["A.java:1:x"]
    state = dict(BASE_STATE, total_llm_calls=1, error_fingerprints=[fp, fp])
    assert decide(state, cfg, now=1010.0) == "budget_exhausted"


# ----------------------------------------------------------------------
# Dollar budget (M6 Task 5): same precedence as the call-count budget, off
# by default.
# ----------------------------------------------------------------------


def test_cost_budget_off_by_default(tmp_path):
    cfg = make_config(tmp_path)
    assert cfg.max_total_cost_usd == 0
    state = dict(BASE_STATE, total_cost_usd=1_000_000.0)  # absurdly high, still ignored
    assert decide(state, cfg, now=1010.0) == "agent"


def test_cost_budget_exhausted(tmp_path):
    cfg = make_config(tmp_path)
    cfg.max_total_cost_usd = 5.0
    state = dict(BASE_STATE, total_cost_usd=5.0)
    assert decide(state, cfg, now=1010.0) == "budget_exhausted"


def test_cost_budget_under_threshold_allows_agent(tmp_path):
    cfg = make_config(tmp_path)
    cfg.max_total_cost_usd = 5.0
    state = dict(BASE_STATE, total_cost_usd=4.99)
    assert decide(state, cfg, now=1010.0) == "agent"


def test_cost_budget_checked_before_retries_exhausted(tmp_path):
    """Same precedence rule as the call-count budget: a global budget check
    (dollar or call-count) always wins over a phase-local cap."""
    cfg = make_config(tmp_path)
    cfg.max_total_cost_usd = 1.0
    cfg.max_retries_per_layer = 2
    state = dict(BASE_STATE, total_cost_usd=1.0, retry_count=2)
    assert decide(state, cfg, now=1010.0) == "budget_exhausted"
