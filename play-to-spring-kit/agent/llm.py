"""OpenRouter-backed chat model factory and a bounded tool-call loop.

The tool loop is deliberately hand-rolled (~50 lines) instead of using
create_react_agent: it gives a hard cap on tool calls, verification stays
outside the agent (the next compile node), and we avoid prebuilt-API churn.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool

from .config import AgentConfig

LOG = logging.getLogger("agent.llm")


def make_model(config: AgentConfig, model_name: str) -> BaseChatModel:
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=model_name,
        api_key=config.api_key,
        base_url=config.base_url,
        timeout=config.llm_timeout_sec,
        max_retries=2,
        temperature=0,
    )


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


def run_tool_loop(
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    system: str,
    user: str,
    max_tool_calls: int,
) -> ToolLoopResult:
    """Run a bounded agentic loop: model <-> tools until no tool calls or cap hit."""
    tool_by_name: dict[str, BaseTool] = {t.name: t for t in tools}
    bound = model.bind_tools(list(tools)) if tools else model
    messages: list[Any] = [SystemMessage(content=system), HumanMessage(content=user)]
    result = ToolLoopResult(final_text="")

    while True:
        ai: AIMessage = bound.invoke(messages)
        result.llm_requests += 1
        usage = getattr(ai, "usage_metadata", None) or {}
        result.input_tokens += usage.get("input_tokens", 0)
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

    result.messages = messages
    return result


UsageWriter = Callable[[dict[str, Any]], None]


def append_usage_log(config: AgentConfig, record: dict[str, Any]) -> None:
    """Append one usage record to <spring-repo>/.migration/llm-usage.json (JSONL)."""
    path = config.migration_dir / "llm-usage.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")
