"""Context-window budgeting for run_tool_loop (M5): config, compact_messages,
and the headless/interactive trigger wiring in agent.llm.run_tool_loop."""

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

import agent.llm as llm_module
from agent.config import AgentConfig
from agent.llm import compact_messages, run_tool_loop


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


class _BudgetFakeModel:
    """Deterministic on len(messages) so it survives LangGraph interrupt replay
    (see Task 4): identical inputs always produce identical outputs."""

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        if len(messages) == 2:
            return AIMessage(
                content="",
                tool_calls=[{"name": "noop", "args": {}, "id": "1"}],
                usage_metadata={"input_tokens": 999_999, "output_tokens": 1, "total_tokens": 1_000_000},
            )
        return AIMessage(
            content="done",
            usage_metadata={"input_tokens": 10, "output_tokens": 1, "total_tokens": 11},
        )


def test_run_tool_loop_headless_auto_compacts_over_budget(monkeypatch, tmp_path):
    monkeypatch.setattr(llm_module, "make_model", lambda config, name: _FakeSummaryModel())
    cfg = AgentConfig(spring_repo=tmp_path, max_agent_context_tokens=100)

    result = run_tool_loop(
        model=_BudgetFakeModel(),
        tools=[],
        system="sys",
        user="task",
        max_tool_calls=5,
        config=cfg,
    )

    assert result.final_text == "done"
    assert result.compactions == 1
    assert result.stopped_by_cap is False


def test_run_tool_loop_under_budget_never_compacts(monkeypatch, tmp_path):
    monkeypatch.setattr(llm_module, "make_model", lambda config, name: _FakeSummaryModel())
    cfg = AgentConfig(spring_repo=tmp_path)  # default 50_000, well above 999_999? no -- use high threshold
    cfg.max_agent_context_tokens = 10_000_000

    result = run_tool_loop(
        model=_BudgetFakeModel(),
        tools=[],
        system="sys",
        user="task",
        max_tool_calls=5,
        config=cfg,
    )

    assert result.final_text == "done"
    assert result.compactions == 0


from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command


def _graph_running_tool_loop(model, cfg, max_tool_calls=10):
    """Minimal 1-node graph so run_tool_loop's interrupt() has a runnable
    context, mirroring how it's really invoked (inside a graph node)."""

    def node(state):
        result = run_tool_loop(
            model=model,
            tools=[],
            system="sys",
            user="task",
            max_tool_calls=max_tool_calls,
            config=cfg,
        )
        return {"final_text": result.final_text, "compactions": result.compactions}

    g = StateGraph(dict)
    g.add_node("n", node)
    g.set_entry_point("n")
    g.add_edge("n", END)
    return g.compile(checkpointer=InMemorySaver())


def test_run_tool_loop_interactive_first_crossing_interrupts(monkeypatch, tmp_path):
    monkeypatch.setattr(llm_module, "make_model", lambda config, name: _FakeSummaryModel())
    cfg = AgentConfig(spring_repo=tmp_path, headless=False, max_agent_context_tokens=100)
    graph = _graph_running_tool_loop(_BudgetFakeModel(), cfg)
    run_cfg = {"configurable": {"thread_id": "t-interactive-1"}}

    first = graph.invoke({}, config=run_cfg)

    assert "__interrupt__" in first
    itr = first["__interrupt__"][0]
    assert itr.value["reason"] == "context_budget"
    assert itr.value["input_tokens"] == 999_999
    assert itr.value["threshold"] == 100


def test_run_tool_loop_interactive_continue_skips_compaction(monkeypatch, tmp_path):
    monkeypatch.setattr(llm_module, "make_model", lambda config, name: _FakeSummaryModel())
    cfg = AgentConfig(spring_repo=tmp_path, headless=False, max_agent_context_tokens=100)
    graph = _graph_running_tool_loop(_BudgetFakeModel(), cfg)
    run_cfg = {"configurable": {"thread_id": "t-interactive-continue"}}

    graph.invoke({}, config=run_cfg)
    final = graph.invoke(Command(resume="continue"), config=run_cfg)

    assert "__interrupt__" not in final
    assert final["final_text"] == "done"
    assert final["compactions"] == 0


class _TwoCrossingsModel:
    """Crosses budget on the 1st AND 2nd tool-call round; 'done' on the 3rd.

    Keyed on len(messages), not a call counter, for the same reason as
    _BudgetFakeModel above -- the node replays from scratch on resume.
    """

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        if len(messages) in (2, 4):
            return AIMessage(
                content="",
                tool_calls=[{"name": "noop", "args": {}, "id": str(len(messages))}],
                usage_metadata={"input_tokens": 999_999, "output_tokens": 1, "total_tokens": 1_000_000},
            )
        return AIMessage(
            content="done",
            usage_metadata={"input_tokens": 10, "output_tokens": 1, "total_tokens": 11},
        )


def test_run_tool_loop_interactive_second_crossing_same_round_auto_compacts(monkeypatch, tmp_path):
    monkeypatch.setattr(llm_module, "make_model", lambda config, name: _FakeSummaryModel())
    cfg = AgentConfig(spring_repo=tmp_path, headless=False, max_agent_context_tokens=100)
    graph = _graph_running_tool_loop(_TwoCrossingsModel(), cfg)
    run_cfg = {"configurable": {"thread_id": "t-interactive-2x"}}

    first = graph.invoke({}, config=run_cfg)
    assert "__interrupt__" in first

    final = graph.invoke(Command(resume="compact"), config=run_cfg)

    assert "__interrupt__" not in final
    assert final["final_text"] == "done"
    assert final["compactions"] == 2


def test_run_tool_loop_interactive_garbage_resume_defaults_to_compact(monkeypatch, tmp_path):
    monkeypatch.setattr(llm_module, "make_model", lambda config, name: _FakeSummaryModel())
    cfg = AgentConfig(spring_repo=tmp_path, headless=False, max_agent_context_tokens=100)
    graph = _graph_running_tool_loop(_BudgetFakeModel(), cfg)
    run_cfg = {"configurable": {"thread_id": "t-interactive-garbage"}}

    graph.invoke({}, config=run_cfg)
    final = graph.invoke(Command(resume="banana"), config=run_cfg)

    assert "__interrupt__" not in final
    assert final["final_text"] == "done"
    assert final["compactions"] == 1
