"""agents/architect.py tests: prompt formatting + one architect round with a
fake model. decisions.md must land under .migration/ -- verifies the FsJail
write-jail widening (tools/fs.py, M6 Task 6) actually reaches it."""

from langchain_core.messages import AIMessage

from agent.agents.architect import (
    DECISIONS_RELATIVE_PATH,
    build_architect_prompt,
    run_architect,
)
from agent.config import AgentConfig


class FakeToolModel:
    def __init__(self, responses):
        self.responses = list(responses)

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        return self.responses.pop(0)


def test_build_architect_prompt_includes_source_inventory():
    source_inventory = {"total_java_files": 12, "by_layer": {"controller": 3, "service": 9}}
    prompt = build_architect_prompt(source_inventory, None)
    assert "12" in prompt
    assert "controller: 3" in prompt


def test_build_architect_prompt_includes_play_surface_gaps():
    play_surface = {
        "coveragePercent": 50.0,
        "unknownCount": 1,
        "paradigmCount": 1,
        "touchpoints": [
            {"construct": "akka.actor.UntypedActor", "location": "Worker.java:1", "classification": "PARADIGM"},
        ],
    }
    prompt = build_architect_prompt(None, play_surface)
    assert "50.0%" in prompt
    assert "akka.actor.UntypedActor" in prompt


def test_build_architect_prompt_handles_missing_inventories():
    prompt = build_architect_prompt(None, None)
    assert "no Play source inventory" in prompt
    assert "no play-surface scan" in prompt


def test_run_architect_writes_decisions_md(tmp_path):
    cfg = AgentConfig(spring_repo=tmp_path)
    model = FakeToolModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {"path": DECISIONS_RELATIVE_PATH, "content": "# Migration Decisions\n"},
                        "id": "1",
                    }
                ],
            ),
            AIMessage(content="done"),
        ]
    )

    edited, result = run_architect(cfg, None, None, attempt=1, model_override=model)

    decisions_path = tmp_path / DECISIONS_RELATIVE_PATH
    assert decisions_path.is_file()
    assert "Migration Decisions" in decisions_path.read_text()
    assert len(edited) == 1
    assert result.tool_calls == 1


def test_run_architect_uses_premium_model_tier(monkeypatch, tmp_path):
    """One-shot, high-stakes scaffold, same rationale as bootstrap -- no
    cheap-first-pass economics to exploit here."""
    seen = {}

    def fake_make_model(config, name):
        seen["name"] = name
        return FakeToolModel([AIMessage(content="done")])

    import agent.agents.architect as architect_module

    monkeypatch.setattr(architect_module, "make_model", fake_make_model)
    cfg = AgentConfig(spring_repo=tmp_path)

    run_architect(cfg, None, None, attempt=1)

    assert seen["name"] == cfg.model_premium
