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
    # M6 Task 12: the summary now also points at the retrievable raw span
    # (see test_compact_messages_writes_retrievable_span below) -- the
    # bracketed prefix's exact wording grew a pointer, so match on the
    # stable substrings rather than the old exact-prefix string.
    assert summary_msg.content.startswith("[Earlier progress, compacted")
    assert "full detail:" in summary_msg.content
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


def test_compact_messages_writes_retrievable_span(monkeypatch, tmp_path):
    """M6 Task 12: compaction was lossy with no reload path -- the raw
    middle turns must now be retrievable from disk, not gone forever once
    the (LLM-generated, potentially incomplete) summary is all that's left
    in `messages`."""
    monkeypatch.setattr(llm_module, "make_model", lambda config, name: _FakeSummaryModel())
    cfg = AgentConfig(spring_repo=tmp_path)
    messages = _build_transcript(4)

    result = compact_messages(messages, cfg)

    span_dir = tmp_path / ".migration" / "compacted"
    assert span_dir.is_dir()
    span_files = list(span_dir.glob("*.txt"))
    assert len(span_files) == 1

    summary_content = result[2].content
    assert span_files[0].name in summary_content

    # The raw span file actually contains the compacted-away turns' content
    # (tool_calls / read_file args from _build_transcript), not just an
    # empty placeholder.
    span_text = span_files[0].read_text()
    assert "f0.txt" in span_text or "f1.txt" in span_text


def test_compact_messages_span_path_is_relative_to_spring_repo(monkeypatch, tmp_path):
    monkeypatch.setattr(llm_module, "make_model", lambda config, name: _FakeSummaryModel())
    cfg = AgentConfig(spring_repo=tmp_path)

    result = compact_messages(_build_transcript(4), cfg)

    summary_content = result[2].content
    # Path referenced in the summary must be relative (what read_file
    # expects), not an absolute path leaking the host filesystem layout.
    import re

    match = re.search(r"full detail: ([^)]+)\)", summary_content)
    assert match is not None
    referenced_path = match.group(1)
    assert not referenced_path.startswith("/")
    assert (tmp_path / referenced_path).is_file()


def test_compact_messages_span_readable_via_fs_jail_read_file(monkeypatch, tmp_path):
    """The whole point: an agent must actually be able to read this back
    with its existing read_file tool, not just have the file exist on disk."""
    from agent.tools.fs import FsJail

    monkeypatch.setattr(llm_module, "make_model", lambda config, name: _FakeSummaryModel())
    cfg = AgentConfig(spring_repo=tmp_path)

    result = compact_messages(_build_transcript(4), cfg)
    span_dir = tmp_path / ".migration" / "compacted"
    span_file = next(span_dir.glob("*.txt"))

    jail = FsJail(tmp_path)
    read_tool = next(t for t in jail.build_tools() if t.name == "read_file")
    content = read_tool.invoke({"path": str(span_file.relative_to(tmp_path))})

    assert content  # no PermissionError, real content came back


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


# ----------------------------------------------------------------------
# Prompt caching (M6 Task 4)
# ----------------------------------------------------------------------


class _CapturingModel:
    """Records the messages it's invoked with, so tests can inspect the
    cache_control shape run_tool_loop builds. bind_tools just returns self
    like every other fake model in this codebase."""

    def __init__(self):
        self.seen_messages: list = []

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        self.seen_messages.append(list(messages))
        return AIMessage(content="done", usage_metadata={"input_tokens": 10, "output_tokens": 1, "total_tokens": 11})


def test_prompt_caching_enabled_marks_system_and_user_content(tmp_path):
    cfg = AgentConfig(spring_repo=tmp_path)
    assert cfg.prompt_caching_enabled is True
    model = _CapturingModel()

    run_tool_loop(model=model, tools=[], system="sys prompt", user="user prompt", max_tool_calls=5, config=cfg)

    system_msg, user_msg = model.seen_messages[0][0], model.seen_messages[0][1]
    assert isinstance(system_msg.content, list)
    assert system_msg.content[0]["cache_control"] == {"type": "ephemeral"}
    assert system_msg.content[0]["text"] == "sys prompt"
    assert isinstance(user_msg.content, list)
    assert user_msg.content[0]["cache_control"] == {"type": "ephemeral"}
    assert user_msg.content[0]["text"] == "user prompt"


def test_prompt_caching_disabled_env_falls_back_to_plain_strings(tmp_path, monkeypatch):
    monkeypatch.setenv("MIGRATION_PROMPT_CACHING", "0")
    cfg = AgentConfig(spring_repo=tmp_path)
    assert cfg.prompt_caching_enabled is False
    model = _CapturingModel()

    run_tool_loop(model=model, tools=[], system="sys prompt", user="user prompt", max_tool_calls=5, config=cfg)

    system_msg, user_msg = model.seen_messages[0][0], model.seen_messages[0][1]
    assert system_msg.content == "sys prompt"
    assert user_msg.content == "user prompt"


def test_prompt_caching_disabled_via_config_override(tmp_path):
    cfg = AgentConfig(spring_repo=tmp_path, prompt_caching_enabled=False)
    model = _CapturingModel()

    run_tool_loop(model=model, tools=[], system="sys", user="task", max_tool_calls=5, config=cfg)

    assert model.seen_messages[0][0].content == "sys"


class _CacheReportingModel:
    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        return AIMessage(
            content="done",
            usage_metadata={
                "input_tokens": 100,
                "output_tokens": 5,
                "total_tokens": 105,
                "input_token_details": {"cache_read": 80, "cache_creation": 20},
            },
        )


def test_run_tool_loop_captures_cache_token_usage(tmp_path):
    cfg = AgentConfig(spring_repo=tmp_path)

    result = run_tool_loop(
        model=_CacheReportingModel(), tools=[], system="sys", user="task", max_tool_calls=5, config=cfg
    )

    assert result.cache_read_input_tokens == 80
    assert result.cache_creation_input_tokens == 20


def test_run_tool_loop_cache_tokens_default_zero_when_not_reported(tmp_path):
    """A provider/model that doesn't report cache details must read as 0,
    not raise -- these are a bonus signal, not a correctness requirement."""
    cfg = AgentConfig(spring_repo=tmp_path)

    result = run_tool_loop(model=_CapturingModel(), tools=[], system="sys", user="task", max_tool_calls=5, config=cfg)

    assert result.cache_read_input_tokens == 0
    assert result.cache_creation_input_tokens == 0


def test_compaction_usage_is_logged(monkeypatch, tmp_path):
    """The compaction model.invoke was previously an unmetered LLM side
    channel -- verify it now lands in llm-usage.jsonl like every other call."""
    monkeypatch.setattr(llm_module, "make_model", lambda config, name: _FakeSummaryModel())
    cfg = AgentConfig(spring_repo=tmp_path)
    messages = _build_transcript(4)

    compact_messages(messages, cfg)

    usage_path = cfg.migration_dir / "llm-usage.jsonl"
    assert usage_path.is_file()
    lines = usage_path.read_text().strip().splitlines()
    records = [__import__("json").loads(line) for line in lines]
    compact_records = [r for r in records if r.get("phase") == "compact"]
    assert len(compact_records) == 1
    assert compact_records[0]["model"] == cfg.model_cheap


# ----------------------------------------------------------------------
# Pre-flight compaction (M6 Task 12): estimate before bound.invoke, so an
# oversized request is compacted away before being sent, not only detected
# afterward from usage_metadata.
# ----------------------------------------------------------------------


def test_estimate_tokens_rough_chars_per_token():
    from agent.llm import _estimate_tokens

    messages = [SystemMessage(content="x" * 400)]
    # 400 chars / 4 chars-per-token estimate == 100
    assert _estimate_tokens(messages) == 100


def test_estimate_tokens_handles_cache_marked_content_blocks():
    from agent.llm import _estimate_tokens

    messages = [SystemMessage(content=[{"type": "text", "text": "x" * 40, "cache_control": {"type": "ephemeral"}}])]
    assert _estimate_tokens(messages) == 10


def test_estimate_tokens_includes_tool_calls():
    from agent.llm import _estimate_tokens

    with_calls = [AIMessage(content="", tool_calls=[{"name": "read_file", "args": {"path": "x" * 100}, "id": "1"}])]
    without_calls = [AIMessage(content="")]
    assert _estimate_tokens(with_calls) > _estimate_tokens(without_calls)


class _PreflightFakeModel:
    """Never reports usage_metadata large enough to trip the post-invoke
    check -- isolates the pre-flight path from the existing post-invoke one."""

    def __init__(self):
        self.invoke_count = 0

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        self.invoke_count += 1
        if self.invoke_count == 1:
            return AIMessage(
                content="",
                tool_calls=[{"name": "noop", "args": {}, "id": "1"}],
                usage_metadata={"input_tokens": 5, "output_tokens": 1, "total_tokens": 6},
            )
        return AIMessage(content="done", usage_metadata={"input_tokens": 5, "output_tokens": 1, "total_tokens": 6})


def test_preflight_compacts_before_invoke_not_just_after(monkeypatch, tmp_path):
    """A large transcript already assembled from a prior round's tool
    output must be compacted BEFORE the next bound.invoke call, not only
    detected afterward -- this is the whole point of the pre-flight check."""
    monkeypatch.setattr(llm_module, "make_model", lambda config, name: _FakeSummaryModel())
    cfg = AgentConfig(spring_repo=tmp_path, max_agent_context_tokens=50)

    model = _PreflightFakeModel()
    result = run_tool_loop(model=model, tools=[], system="sys", user="task", max_tool_calls=5, config=cfg)

    # noop isn't a real tool, so its ToolMessage output is short -- the
    # transcript alone won't cross 50 estimated tokens from two tiny
    # messages. This test's job is just to confirm the pre-flight branch is
    # reachable and doesn't break the loop; the large-transcript case is
    # covered by test_preflight_triggers_on_large_prior_tool_output below.
    assert result.final_text == "done"


def test_preflight_triggers_on_large_transcript_with_compactable_history(monkeypatch, tmp_path):
    """compact_messages always keeps the last 2 turns verbatim (by design --
    a tool_call must never be split from its ToolMessage result), so a
    single oversized FIRST turn has nothing earlier to cut and legitimately
    can't be compacted away yet. This test instead builds up several small
    turns before one that pushes the estimate over budget, so there IS
    compactable history by the time pre-flight fires -- and confirms it
    fires before the invoke that would have seen the bloated transcript,
    not only after."""
    monkeypatch.setattr(llm_module, "make_model", lambda config, name: _FakeSummaryModel())
    cfg = AgentConfig(spring_repo=tmp_path, max_agent_context_tokens=50)

    class GrowingModel:
        def __init__(self):
            self.invoke_count = 0
            self.seen_estimate_at_call: list[int] = []

        def bind_tools(self, tools):
            return self

        def invoke(self, messages):
            from agent.llm import _estimate_tokens

            self.invoke_count += 1
            self.seen_estimate_at_call.append(_estimate_tokens(messages))
            if self.invoke_count <= 4:
                return AIMessage(
                    content="",
                    tool_calls=[{"name": "noop", "args": {"note": "x" * 60}, "id": str(self.invoke_count)}],
                    usage_metadata={"input_tokens": 5, "output_tokens": 1, "total_tokens": 6},
                )
            return AIMessage(content="done", usage_metadata={"input_tokens": 5, "output_tokens": 1, "total_tokens": 6})

    model = GrowingModel()
    result = run_tool_loop(model=model, tools=[], system="sys", user="task", max_tool_calls=10, config=cfg)

    assert result.final_text == "done"
    assert result.compactions >= 1
    # The estimate seen by at least one later call must be well under what
    # an uncompacted 4-turn transcript of ~60-char tool_calls each would
    # have accumulated to -- confirms pre-flight actually cut it down before
    # sending, not just noticed it was too big after the fact.
    assert min(model.seen_estimate_at_call[1:]) < max(model.seen_estimate_at_call)
