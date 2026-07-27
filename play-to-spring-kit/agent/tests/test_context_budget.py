"""Context-window budgeting for run_tool_loop (M5): config, compact_messages,
and the headless/interactive trigger wiring in agent.llm.run_tool_loop."""

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

import agent.llm as llm_module
from agent.config import AgentConfig
from agent.llm import compact_messages


def test_max_agent_context_tokens_default(tmp_path):
    cfg = AgentConfig(spring_repo=tmp_path)
    assert cfg.max_agent_context_tokens == 50_000


def test_max_agent_context_tokens_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("MAX_AGENT_CONTEXT_TOKENS", "12345")
    cfg = AgentConfig(spring_repo=tmp_path)
    assert cfg.max_agent_context_tokens == 12345


class _FakeSummaryModel:
    def invoke(self, messages):
        return AIMessage(content="SUMMARY")


def _build_transcript(n_turns: int) -> list:
    """system, task, then n_turns of (AIMessage w/ tool_call, ToolMessage)."""
    messages = [SystemMessage(content="sys"), HumanMessage(content="task")]
    for i in range(n_turns):
        messages.append(
            AIMessage(
                content="",
                tool_calls=[{"name": "read_file", "args": {"path": f"f{i}.txt"}, "id": str(i)}],
            )
        )
        messages.append(ToolMessage(content=f"contents {i}", tool_call_id=str(i)))
    return messages


def test_compact_messages_keeps_system_and_task_untouched(monkeypatch, tmp_path):
    monkeypatch.setattr(llm_module, "make_model", lambda config, name: _FakeSummaryModel())
    cfg = AgentConfig(spring_repo=tmp_path)
    messages = _build_transcript(4)

    result = compact_messages(messages, cfg)

    assert result[0] is messages[0]
    assert result[1] is messages[1]


def test_compact_messages_keeps_last_two_turns_verbatim(monkeypatch, tmp_path):
    monkeypatch.setattr(llm_module, "make_model", lambda config, name: _FakeSummaryModel())
    cfg = AgentConfig(spring_repo=tmp_path)
    messages = _build_transcript(4)

    result = compact_messages(messages, cfg)

    # last 2 turns = last 4 messages (2 AI+Tool pairs) of the original transcript
    assert result[-4:] == messages[-4:]


def test_compact_messages_inserts_summary_between_task_and_tail(monkeypatch, tmp_path):
    monkeypatch.setattr(llm_module, "make_model", lambda config, name: _FakeSummaryModel())
    cfg = AgentConfig(spring_repo=tmp_path)
    messages = _build_transcript(4)

    result = compact_messages(messages, cfg)

    assert len(result) == 2 + 1 + 4  # head(2) + summary(1) + kept tail (2 turns = 4 msgs)
    summary_msg = result[2]
    assert isinstance(summary_msg, HumanMessage)
    assert summary_msg.content.startswith("[Earlier progress, compacted]")
    assert "SUMMARY" in summary_msg.content


def test_compact_messages_noop_when_two_or_fewer_turns(tmp_path):
    cfg = AgentConfig(spring_repo=tmp_path)
    messages = _build_transcript(2)

    result = compact_messages(messages, cfg)

    assert result == messages


def test_compact_messages_uses_cheap_tier_model(monkeypatch, tmp_path):
    seen = {}

    def fake_make_model(config, name):
        seen["name"] = name
        return _FakeSummaryModel()

    monkeypatch.setattr(llm_module, "make_model", fake_make_model)
    cfg = AgentConfig(spring_repo=tmp_path)

    compact_messages(_build_transcript(4), cfg)

    assert seen["name"] == cfg.model_cheap
