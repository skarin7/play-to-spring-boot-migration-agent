"""flag_for_manual_review (tools/fs.py) + run_tool_loop's generic detection of
it (llm.py) + guards.decide() short-circuiting on it + slice_finalize_node
preferring it over the deterministic package-name guess.

This is the model-driven alternative to hardcoding a package-name blocklist
(legacy_logic.unmappable_framework_packages) for every framework that has no
Spring/Jakarta equivalent: the agent calls the tool itself when it recognizes
it's stuck, instead of the harness guessing from a fixed list."""

from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from agent.config import AgentConfig
from agent.guards import decide
from agent.llm import run_tool_loop
from agent.nodes.slice_pipeline import _manual_intervention_note
from agent.tools.fs import MANUAL_REVIEW_PREFIX, FsJail


@tool
def flag_for_manual_review(reason: str) -> str:
    """test double matching tools/fs.py's real tool"""
    return f"{MANUAL_REVIEW_PREFIX}{reason}"


class _FlagsThenStopsModel:
    def __init__(self):
        self.calls = 0

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        self.calls += 1
        if self.calls == 1:
            return AIMessage(
                content="",
                tool_calls=[{"name": "flag_for_manual_review", "args": {"reason": "needs a redesign"}, "id": "1"}],
                usage_metadata={"input_tokens": 10, "output_tokens": 1, "total_tokens": 11},
            )
        # Should never be reached -- the loop must stop right after the flag.
        return AIMessage(content="should not get here", usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2})


def test_run_tool_loop_stops_and_captures_reason_on_flag(tmp_path):
    cfg = AgentConfig(spring_repo=tmp_path)
    model = _FlagsThenStopsModel()

    result = run_tool_loop(
        model=model, tools=[flag_for_manual_review], system="sys", user="task", max_tool_calls=8, config=cfg
    )

    assert result.manual_review_reason == "needs a redesign"
    assert result.stopped_by_cap is False
    assert model.calls == 1  # loop stopped right after the flag, never asked for round 2


def test_real_fsjail_tool_returns_expected_prefix(tmp_path):
    jail = FsJail(tmp_path)
    tools_by_name = {t.name: t for t in jail.build_tools()}

    out = tools_by_name["flag_for_manual_review"].invoke({"reason": "Akka streams has no Spring equivalent"})

    assert out == f"{MANUAL_REVIEW_PREFIX}Akka streams has no Spring equivalent"


def test_guard_stops_immediately_when_agent_flagged(tmp_path):
    cfg = AgentConfig(spring_repo=tmp_path)
    cfg.api_key = "test-key"
    # retry_count=0, no fingerprints, plenty of budget -- every other check
    # would say "agent"; agent_manual_review_reason must override all of them.
    state = {
        "retry_count": 0,
        "total_llm_calls": 0,
        "slice_started_at": 1000.0,
        "error_fingerprints": [],
        "last_clusters": [{"root_cause": "x"}],
        "agent_manual_review_reason": "needs a redesign",
    }
    assert decide(state, cfg, now=1001.0) == "looping"


def test_manual_intervention_note_prefers_agent_reason_over_regex_guess(tmp_path):
    state = {
        "agent_manual_review_reason": "Akka streams has no Spring equivalent, needs redesign",
        "last_compile": {"errors": [{"file": "A.java", "line": 1, "message": "package org.apache.pekko does not exist"}]},
    }
    assert _manual_intervention_note(state) == "Akka streams has no Spring equivalent, needs redesign"


def test_manual_intervention_note_falls_back_to_regex_guess_when_not_flagged(tmp_path):
    state = {
        "last_compile": {"errors": [{"file": "A.java", "line": 1, "message": "package org.apache.pekko does not exist"}]},
    }
    note = _manual_intervention_note(state)
    assert note is not None
    assert "org.apache.pekko" in note
