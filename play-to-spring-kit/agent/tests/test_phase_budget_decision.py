"""nodes/common.py:phase_budget_decision -- the shared three-way decision for
every bounded per-phase LLM loop (routes, config_mapping, runtime_wiring).
Call-count budget already had implicit coverage via the graph-level phase
tests; this adds direct unit coverage plus the M6 Task 5 dollar-budget branch,
same precedence rule as guards.decide."""

from pathlib import Path

from agent.config import AgentConfig
from agent.nodes.common import phase_budget_decision


def make_config(tmp_path: Path, **kw) -> AgentConfig:
    cfg = AgentConfig(spring_repo=tmp_path, **kw)
    cfg.api_key = "test-key"
    return cfg


def test_continue_when_under_every_cap(tmp_path):
    cfg = make_config(tmp_path)
    state = {"total_llm_calls": 0, "total_cost_usd": 0.0}
    assert phase_budget_decision(state, cfg, attempts=0, max_attempts=3) == "continue"


def test_call_count_budget_exhausted(tmp_path):
    cfg = make_config(tmp_path, max_total_llm_calls=5)
    state = {"total_llm_calls": 5}
    assert phase_budget_decision(state, cfg, attempts=0, max_attempts=3) == "budget_exhausted"


def test_attempts_exhausted(tmp_path):
    cfg = make_config(tmp_path)
    state = {"total_llm_calls": 0}
    assert phase_budget_decision(state, cfg, attempts=3, max_attempts=3) == "attempts_exhausted"


def test_cost_budget_off_by_default(tmp_path):
    cfg = make_config(tmp_path)
    state = {"total_llm_calls": 0, "total_cost_usd": 999.0}
    assert phase_budget_decision(state, cfg, attempts=0, max_attempts=3) == "continue"


def test_cost_budget_exhausted(tmp_path):
    cfg = make_config(tmp_path, max_total_cost_usd=2.0)
    state = {"total_llm_calls": 0, "total_cost_usd": 2.0}
    assert phase_budget_decision(state, cfg, attempts=0, max_attempts=3) == "budget_exhausted"


def test_cost_budget_checked_before_attempts_exhausted(tmp_path):
    cfg = make_config(tmp_path, max_total_cost_usd=1.0)
    state = {"total_llm_calls": 0, "total_cost_usd": 1.0}
    assert phase_budget_decision(state, cfg, attempts=3, max_attempts=3) == "budget_exhausted"


def test_call_count_checked_before_cost_budget():
    """Order between the two global budgets doesn't matter for correctness
    (both abort the run identically), but call-count is checked first in the
    implementation -- verify a call-count-only breach still resolves
    correctly even when cost is also, separately, under its own cap."""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        cfg = make_config(Path(d), max_total_llm_calls=1, max_total_cost_usd=100.0)
        state = {"total_llm_calls": 1, "total_cost_usd": 0.0}
        assert phase_budget_decision(state, cfg, attempts=0, max_attempts=3) == "budget_exhausted"
