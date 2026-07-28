# Context-Window Budgeting for `run_tool_loop` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `run_tool_loop` (`play-to-spring-kit/agent/llm.py`) a token-based context budget: auto-compact silently in headless mode, ask once per round via `interrupt()` in interactive mode, per the design in `docs/superpowers/specs/2026-07-26-context-window-budgeting-design.md`.

**Architecture:** One new `AgentConfig` field (`max_agent_context_tokens`), one new pure function (`compact_messages`), and budget-check/compaction wiring inserted into the existing `run_tool_loop` while-loop, gated by `config.headless`. All 5 LLM agents (bootstrap, compile_fix, config_mapping, routes, runtime_wiring) get it for free by passing `config=` through their existing `run_tool_loop` call.

**Tech Stack:** Python, LangChain core messages (`AIMessage`/`HumanMessage`/`SystemMessage`/`ToolMessage`), LangGraph `interrupt()`, pytest.

**Baseline:** `cd play-to-spring-kit && .venv/bin/python -m pytest agent/tests -q` currently passes 130/130. Re-run after every task.

---

### Task 1: `max_agent_context_tokens` config field

**Files:**
- Modify: `play-to-spring-kit/agent/config.py:91` (add field next to `max_agent_tool_calls`)
- Test: `play-to-spring-kit/agent/tests/test_context_budget.py` (new file)

- [ ] **Step 1: Write the failing tests**

```python
"""Context-window budgeting for run_tool_loop (M5): config, compact_messages,
and the headless/interactive trigger wiring in agent.llm.run_tool_loop."""

from agent.config import AgentConfig


def test_max_agent_context_tokens_default(tmp_path):
    cfg = AgentConfig(spring_repo=tmp_path)
    assert cfg.max_agent_context_tokens == 50_000


def test_max_agent_context_tokens_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("MAX_AGENT_CONTEXT_TOKENS", "12345")
    cfg = AgentConfig(spring_repo=tmp_path)
    assert cfg.max_agent_context_tokens == 12345
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd play-to-spring-kit && .venv/bin/python -m pytest agent/tests/test_context_budget.py -v`
Expected: FAIL with `AttributeError: 'AgentConfig' object has no attribute 'max_agent_context_tokens'`

- [ ] **Step 3: Add the field**

In `play-to-spring-kit/agent/config.py`, right after line 91 (`max_agent_tool_calls: int = field(...)`):

```python
    max_agent_context_tokens: int = field(
        default_factory=lambda: _env_int("MAX_AGENT_CONTEXT_TOKENS", 50_000)
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd play-to-spring-kit && .venv/bin/python -m pytest agent/tests/test_context_budget.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add play-to-spring-kit/agent/config.py play-to-spring-kit/agent/tests/test_context_budget.py
git commit -m "Add MAX_AGENT_CONTEXT_TOKENS config field"
```

---

### Task 2: `compact_messages()` — pure turn-boundary-safe compaction

**Files:**
- Modify: `play-to-spring-kit/agent/llm.py` (add `compactions` field to `ToolLoopResult`, add `compact_messages()`)
- Test: `play-to-spring-kit/agent/tests/test_context_budget.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `play-to-spring-kit/agent/tests/test_context_budget.py`:

```python
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

import agent.llm as llm_module
from agent.llm import compact_messages


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
```

Add the missing import at the top of the test file (alongside the existing `AgentConfig` import):

```python
from agent.config import AgentConfig
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd play-to-spring-kit && .venv/bin/python -m pytest agent/tests/test_context_budget.py -v`
Expected: FAIL with `ImportError: cannot import name 'compact_messages' from 'agent.llm'`

- [ ] **Step 3: Implement `compact_messages` and the `compactions` field**

In `play-to-spring-kit/agent/llm.py`, add `compactions: int = 0` to `ToolLoopResult`:

```python
@dataclass
class ToolLoopResult:
    final_text: str
    llm_requests: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    stopped_by_cap: bool = False
    compactions: int = 0
    messages: list[Any] = field(default_factory=list)
```

Then add, after `ToolLoopResult` and before `run_tool_loop`:

```python
_COMPACT_INSTRUCTION = (
    "You are compacting the middle of an agent's tool-use transcript so it "
    "keeps working inside a smaller context window. Summarize, concisely but "
    "completely: files read, edits made, errors seen so far, and the "
    "remaining plan. Do not invent anything not present in the transcript."
)


def _render_messages_for_summary(messages: Sequence[Any]) -> str:
    lines: list[str] = []
    for m in messages:
        role = type(m).__name__
        content = m.content if isinstance(m.content, str) else str(m.content)
        tool_calls = getattr(m, "tool_calls", None)
        if tool_calls:
            lines.append(f"{role} tool_calls={tool_calls}")
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _split_into_turns(messages: Sequence[Any]) -> list[list[Any]]:
    """Group messages into turns, each starting at an AIMessage."""
    turns: list[list[Any]] = []
    current: list[Any] = []
    for m in messages:
        if isinstance(m, AIMessage):
            if current:
                turns.append(current)
            current = [m]
        else:
            current.append(m)
    if current:
        turns.append(current)
    return turns


def compact_messages(messages: list[Any], config: AgentConfig) -> list[Any]:
    """Summarize the middle of a tool-loop transcript to reclaim context budget.

    messages[0] (system) and messages[1] (task) are preserved untouched. The
    last 2 complete AI+Tool turns are kept verbatim as a tail -- the cut point
    always lands on a full turn boundary so a tool_call is never split from
    its ToolMessage result. Everything in between is replaced by one
    synthetic HumanMessage holding a cheap-tier-model summary.
    """
    head = list(messages[:2])
    turns = _split_into_turns(messages[2:])
    if len(turns) <= 2:
        return list(messages)

    middle_turns, tail_turns = turns[:-2], turns[-2:]
    middle = [m for turn in middle_turns for m in turn]
    tail = [m for turn in tail_turns for m in turn]

    model = make_model(config, config.model_cheap)
    summary_response = model.invoke(
        [
            SystemMessage(content=_COMPACT_INSTRUCTION),
            HumanMessage(content=_render_messages_for_summary(middle)),
        ]
    )
    summary = (
        summary_response.content
        if isinstance(summary_response.content, str)
        else str(summary_response.content)
    )

    return head + [HumanMessage(content=f"[Earlier progress, compacted]\n{summary}")] + tail
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd play-to-spring-kit && .venv/bin/python -m pytest agent/tests/test_context_budget.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add play-to-spring-kit/agent/llm.py play-to-spring-kit/agent/tests/test_context_budget.py
git commit -m "Add compact_messages() for turn-boundary-safe transcript compaction"
```

---

### Task 3: Wire the budget check into `run_tool_loop` (headless auto-compact)

**Files:**
- Modify: `play-to-spring-kit/agent/llm.py` (`run_tool_loop` signature + body)
- Test: `play-to-spring-kit/agent/tests/test_context_budget.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `play-to-spring-kit/agent/tests/test_context_budget.py`:

```python
from agent.llm import run_tool_loop


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd play-to-spring-kit && .venv/bin/python -m pytest agent/tests/test_context_budget.py -v`
Expected: FAIL with `TypeError: run_tool_loop() missing 1 required keyword-only argument: 'config'` (or similar — `config` doesn't exist as a parameter yet)

- [ ] **Step 3: Wire the trigger into `run_tool_loop`**

Replace the `run_tool_loop` signature and body in `play-to-spring-kit/agent/llm.py`:

```python
def run_tool_loop(
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    system: str,
    user: str,
    max_tool_calls: int,
    config: AgentConfig,
) -> ToolLoopResult:
    """Run a bounded agentic loop: model <-> tools until no tool calls or cap hit."""
    tool_by_name: dict[str, BaseTool] = {t.name: t for t in tools}
    bound = model.bind_tools(list(tools)) if tools else model
    messages: list[Any] = [SystemMessage(content=system), HumanMessage(content=user)]
    result = ToolLoopResult(final_text="")
    asked_this_round = False

    while True:
        ai: AIMessage = bound.invoke(messages)
        result.llm_requests += 1
        usage = getattr(ai, "usage_metadata", None) or {}
        current_input_tokens = usage.get("input_tokens", 0)
        result.input_tokens += current_input_tokens
        result.output_tokens += usage.get("output_tokens", 0)
        messages.append(ai)

        tool_calls = getattr(ai, "tool_calls", None) or []
        if not tool_calls:
            result.final_text = ai.content if isinstance(ai.content, str) else str(ai.content)
            break

        for call in tool_calls:
            result.tool_calls += 1
            name = call.get("name", "")
            args = call.get("args", {}) or {}
            tool = tool_by_name.get(name)
            if tool is None:
                output = f"error: unknown tool {name!r}"
            else:
                try:
                    output = tool.invoke(args)
                except Exception as exc:  # tool errors go back to the model
                    output = f"error: {exc}"
            if not isinstance(output, str):
                output = json.dumps(output, default=str)
            messages.append(ToolMessage(content=output, tool_call_id=call.get("id", "")))

        if result.tool_calls >= max_tool_calls:
            result.stopped_by_cap = True
            LOG.warning("tool loop stopped: cap of %d tool calls reached", max_tool_calls)
            break

        if current_input_tokens > config.max_agent_context_tokens:
            do_compact = True
            if not config.headless:
                if not asked_this_round:
                    asked_this_round = True
                    decision = interrupt(
                        {
                            "reason": "context_budget",
                            "input_tokens": current_input_tokens,
                            "threshold": config.max_agent_context_tokens,
                        }
                    )
                    normalized = decision if isinstance(decision, str) else str(decision)
                    do_compact = normalized.strip().lower() != "continue"
                # else: already asked this round -- auto-compact silently.
            if do_compact:
                messages = compact_messages(messages, config)
                result.compactions += 1
                LOG.info(
                    "tool loop compacted: input_tokens=%d threshold=%d",
                    current_input_tokens,
                    config.max_agent_context_tokens,
                )

    result.messages = messages
    return result
```

Add the `interrupt` import at the top of `play-to-spring-kit/agent/llm.py`, alongside the other imports:

```python
from langgraph.types import interrupt
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd play-to-spring-kit && .venv/bin/python -m pytest agent/tests/test_context_budget.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add play-to-spring-kit/agent/llm.py play-to-spring-kit/agent/tests/test_context_budget.py
git commit -m "Wire context-budget check into run_tool_loop (headless auto-compact)"
```

---

### Task 4: Interactive `interrupt()` behavior (first crossing asks, rest auto-compact)

**Files:**
- Test: `play-to-spring-kit/agent/tests/test_context_budget.py` (append) — no further `llm.py` changes needed; Task 3 already wired the interactive branch.

- [ ] **Step 1: Write the failing tests**

Append to `play-to-spring-kit/agent/tests/test_context_budget.py`:

```python
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
    """Crosses budget on the 1st AND 2nd tool-call round; 'done' on the 3rd."""

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
```

- [ ] **Step 2: Run tests to verify they fail (or pass — confirm intentionally)**

Run: `cd play-to-spring-kit && .venv/bin/python -m pytest agent/tests/test_context_budget.py -v -k interactive`
Expected: These should already PASS, since Task 3 implemented the full interactive branch in `run_tool_loop`. This step is a verification, not a red-then-green cycle — if any of these fail, the interactive branch logic from Task 3 has a bug; fix `llm.py` before proceeding (do not adjust the test's expectations to match broken behavior).

- [ ] **Step 3: Run the full new test file**

Run: `cd play-to-spring-kit && .venv/bin/python -m pytest agent/tests/test_context_budget.py -v`
Expected: 13 passed

- [ ] **Step 4: Commit**

```bash
git add play-to-spring-kit/agent/tests/test_context_budget.py
git commit -m "Add interactive interrupt() coverage for context-budget crossings"
```

---

### Task 5: Wire `config=` through the 5 agent call sites + usage-log visibility

**Files:**
- Modify: `play-to-spring-kit/agent/agents/bootstrap.py:83-103`
- Modify: `play-to-spring-kit/agent/agents/compile_fix.py:61-82`
- Modify: `play-to-spring-kit/agent/agents/config_mapping.py:75-96`
- Modify: `play-to-spring-kit/agent/agents/routes.py:69-90`
- Modify: `play-to-spring-kit/agent/agents/runtime_wiring.py:96-116`

- [ ] **Step 1: `bootstrap.py`**

In `play-to-spring-kit/agent/agents/bootstrap.py`, add `config=config,` to the `run_tool_loop` call and `"compactions": result.compactions,` to `append_usage_log`:

```python
    result = run_tool_loop(
        model=model,
        tools=jail.build_tools(),
        system=builder.system_prompt(),
        user=prompt,
        max_tool_calls=config.max_agent_tool_calls * 2,  # scaffolding 3 files needs more room than a fix round
        config=config,
    )
    append_usage_log(
        config,
        {
            "ts": started,
            "phase": "bootstrap",
            "attempt": attempt,
            "model": config.model_premium,
            "llm_requests": result.llm_requests,
            "tool_calls": result.tool_calls,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "compactions": result.compactions,
            "edited_files": [str(p) for p in jail.edited_files],
        },
    )
```

- [ ] **Step 2: `compile_fix.py`**

In `play-to-spring-kit/agent/agents/compile_fix.py`:

```python
    result = run_tool_loop(
        model=model,
        tools=jail.build_tools(),
        system=system,
        user=user,
        max_tool_calls=config.max_agent_tool_calls,
        config=config,
    )
    append_usage_log(
        config,
        {
            "ts": started,
            "phase": "compile_fix",
            "slice": slice_id,
            "model": model_name,
            "retry": retry_count,
            "llm_requests": result.llm_requests,
            "tool_calls": result.tool_calls,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "compactions": result.compactions,
            "edited_files": [str(p) for p in jail.edited_files],
        },
    )
```

- [ ] **Step 3: `config_mapping.py`**

In `play-to-spring-kit/agent/agents/config_mapping.py`:

```python
    result = run_tool_loop(
        model=model,
        tools=jail.build_tools(),
        system=system,
        user=_user_prompt(leftover),
        max_tool_calls=config.max_agent_tool_calls,
        config=config,
    )
    append_usage_log(
        config,
        {
            "ts": started,
            "phase": "config_mapping",
            "attempt": attempt,
            "model": config.model_cheap,
            "leftover_count": len(leftover),
            "llm_requests": result.llm_requests,
            "tool_calls": result.tool_calls,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "compactions": result.compactions,
            "edited_files": [str(p) for p in jail.edited_files],
        },
    )
```

- [ ] **Step 4: `routes.py`**

In `play-to-spring-kit/agent/agents/routes.py`:

```python
    result = run_tool_loop(
        model=model,
        tools=jail.build_tools(),
        system=SYSTEM_PROMPT,
        user=_user_prompt(unmapped),
        max_tool_calls=config.max_agent_tool_calls,
        config=config,
    )
    append_usage_log(
        config,
        {
            "ts": started,
            "phase": "routes",
            "attempt": attempt,
            "model": config.model_cheap,
            "unmapped_count": len(unmapped),
            "llm_requests": result.llm_requests,
            "tool_calls": result.tool_calls,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "compactions": result.compactions,
            "edited_files": [str(p) for p in jail.edited_files],
        },
    )
```

- [ ] **Step 5: `runtime_wiring.py`**

In `play-to-spring-kit/agent/agents/runtime_wiring.py`:

```python
    result = run_tool_loop(
        model=model,
        tools=jail.build_tools(),
        system=system,
        user=_user_prompt(boot_log_tail),
        max_tool_calls=config.max_agent_tool_calls,
        config=config,
    )
    append_usage_log(
        config,
        {
            "ts": started,
            "phase": "runtime_wiring",
            "attempt": attempt,
            "model": model_name,
            "llm_requests": result.llm_requests,
            "tool_calls": result.tool_calls,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "compactions": result.compactions,
            "edited_files": [str(p) for p in jail.edited_files],
        },
    )
```

- [ ] **Step 6: Run the full regression suite**

Run: `cd play-to-spring-kit && .venv/bin/python -m pytest agent/tests -q`
Expected: 130 passed (same count as the pre-existing baseline — these 5 files' own tests, e.g. `test_bootstrap_agent.py`, `test_routes_agent.py`, `test_config_mapping_agent.py`, `test_runtime_wiring_agent.py`, exercise `run_tool_loop` via `model_override` and will fail loudly with a missing-`config`-argument `TypeError` if any call site was missed)

- [ ] **Step 7: Commit**

```bash
git add play-to-spring-kit/agent/agents/bootstrap.py play-to-spring-kit/agent/agents/compile_fix.py play-to-spring-kit/agent/agents/config_mapping.py play-to-spring-kit/agent/agents/routes.py play-to-spring-kit/agent/agents/runtime_wiring.py
git commit -m "Pass config through to run_tool_loop from all 5 agents; log compactions"
```

---

### Task 6: Docs — `MAX_AGENT_CONTEXT_TOKENS` env var row

**Files:**
- Modify: `docs/langgraph-engine.md:158` (env var reference table)

- [ ] **Step 1: Add the row**

In `docs/langgraph-engine.md`, after the `MAX_AGENT_TOOL_CALLS` row (line 158):

```markdown
| `MAX_AGENT_CONTEXT_TOKENS` | `50000` | Context-budget threshold (input tokens) before `run_tool_loop` auto-compacts (headless) or asks once via `interrupt()` (`--interactive`). |
```

- [ ] **Step 2: Commit**

```bash
git add docs/langgraph-engine.md
git commit -m "Document MAX_AGENT_CONTEXT_TOKENS in langgraph-engine.md"
```

---

### Task 7: Full regression pass

**Files:** none (verification only)

- [ ] **Step 1: Run the entire agent test suite**

Run: `cd play-to-spring-kit && .venv/bin/python -m pytest agent/tests -q`
Expected: `143 passed` (130 baseline + 13 new tests in `test_context_budget.py`)

- [ ] **Step 2: If everything passes, no further commit needed** (Task 5's commit already includes the final code state; this step is a checkpoint before declaring the plan complete).

---

## Self-Review Notes

- **Spec coverage:** new config field (Task 1), `compact_messages` turn-boundary/preservation/cheap-tier behavior (Task 2), headless auto-compact trigger (Task 3), interactive first-ask + same-round auto-compact + default-to-compact-on-garbage-input (Task 4), all 5 agents wired + `compactions` in `llm-usage.jsonl` (Task 5). Env var docs (Task 6) is a small addition beyond the spec's literal text but matches the existing table's convention for every other guardrail env var. Out-of-scope items (per-model % thresholds, dedicated graph node, cheap truncation) are intentionally not tasked.
- **Placeholder scan:** none — every step has complete code.
- **Type consistency:** `compact_messages(messages: list[Any], config: AgentConfig) -> list[Any]` matches its Task 2 definition and Task 3/4 call site (`messages = compact_messages(messages, config)`). `ToolLoopResult.compactions: int` matches all read/increment sites. `run_tool_loop(..., config: AgentConfig)` signature is consistent across Tasks 3-5.
