"""OpenRouter-backed chat model factory and a bounded tool-call loop.

The tool loop is deliberately hand-rolled (~50 lines) instead of using
create_react_agent: it gives a hard cap on tool calls, verification stays
outside the agent (the next compile node), and we avoid prebuilt-API churn.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.types import interrupt

from .config import AgentConfig
from .tools.fs import MANUAL_REVIEW_PREFIX

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
    # Set when a tool call returns fs.MANUAL_REVIEW_PREFIX (e.g.
    # flag_for_manual_review) -- the agent's own diagnosis that this needs a
    # redesign, not further incremental edits. Generic string-prefix
    # convention so run_tool_loop stays tool-set-agnostic (no FsJail import).
    manual_review_reason: str | None = None
    # M6 Task 4: cache read/creation token totals, so the caching win is
    # measured rather than assumed. Zero on a model/provider that doesn't
    # report them, never an error -- these are a bonus signal, not a
    # correctness requirement.
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    # M6 Task 5: total cost across every round of this loop, priced via
    # pricing.cost_usd(model_name, ...). None when model_name has no
    # rate-table entry -- distinct from 0.0 (a real, priced, zero-cost call
    # can't happen, so None unambiguously means "unpriced", never "free").
    total_cost_usd: float | None = 0.0


def _cache_marked_content(text: str) -> list[dict[str, Any]]:
    """One content block carrying an Anthropic cache_control breakpoint,
    passed through OpenRouter. LangChain's message content accepts a list of
    content blocks in this shape for providers that support it."""
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


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

    # M6 Task 4/5: this model.invoke was previously an unmetered LLM side
    # channel -- it costs real money and, unlike every other LLM call in this
    # codebase, went through neither the usage log nor any budget counter.
    # It still isn't counted against total_llm_calls (compaction is plumbing,
    # not a fix-round attempt), but it must at least be visible in the same
    # place every other call's cost shows up.
    usage = getattr(summary_response, "usage_metadata", None) or {}
    append_usage_log(
        config,
        {
            "ts": time.time(),
            "phase": "compact",
            "model": config.model_cheap,
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
        },
    )

    # M6 Task 12: previously the raw middle turns were dropped from
    # `messages` forever, with only the (lossy, LLM-generated) summary
    # surviving -- llm-debug.jsonl has each round's raw content, but nothing
    # ever pointed back to it or made it retrievable in one place. Write the
    # raw compacted-away span to its own file under .migration/compacted/ --
    # already inside FsJail's writable/readable area (Task 6 widened it for
    # decisions.md) -- and name that path in the synthetic summary message so
    # the agent can read_file it back if the summary turns out to be missing
    # something it needs.
    span_path = _write_compacted_span(config, middle)
    pointer = f" (full detail: {span_path})" if span_path else ""

    return head + [
        HumanMessage(content=f"[Earlier progress, compacted{pointer}]\n{summary}")
    ] + tail


def _write_compacted_span(config: AgentConfig, middle: list[Any]) -> str | None:
    """Writes the raw (uncompacted) middle turns to a retrievable file.
    Returns the path relative to spring_repo (what the agent should pass to
    read_file), or None if the write itself fails -- compaction must still
    succeed even if this best-effort archival step can't."""
    try:
        rel_dir = Path(".migration") / "compacted"
        abs_dir = config.spring_repo / rel_dir
        abs_dir.mkdir(parents=True, exist_ok=True)
        rel_path = rel_dir / f"{uuid.uuid4().hex[:8]}.txt"
        (config.spring_repo / rel_path).write_text(_render_messages_for_summary(middle), encoding="utf-8")
        return str(rel_path)
    except OSError:
        return None


# M6 Task 12: rough chars-per-token ratio for a pre-flight estimate, no
# tokenizer dependency -- 4 is the commonly-cited average for English/code
# mixed text across most tokenizers. This is deliberately conservative
# (undercounts less than it overcounts) since the cost of a false positive
# (compacting slightly early) is far cheaper than the cost of a false
# negative (sending an oversized request anyway, which is the exact
# post-hoc-only behavior this estimate exists to reduce).
_CHARS_PER_TOKEN_ESTIMATE = 4


def _estimate_tokens(messages: Sequence[Any]) -> int:
    total_chars = 0
    for m in messages:
        content = m.content
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    total_chars += len(str(block.get("text", block)))
                else:
                    total_chars += len(str(block))
        else:
            total_chars += len(str(content))
        tool_calls = getattr(m, "tool_calls", None)
        if tool_calls:
            total_chars += len(str(tool_calls))
    return total_chars // _CHARS_PER_TOKEN_ESTIMATE


def _append_llm_debug(config: AgentConfig, request_id: str, record: dict[str, Any]) -> Path:
    """Append one full-detail record (prompts/tool args/tool outputs) to
    <spring-repo>/.migration/llm-debug.jsonl. Kept out of the stdout logger
    since these payloads can be large -- inspect the file for the exact
    request/response content behind a given round; stdout only gets a short
    per-round summary via LOG, tagged with the same request_id so `grep
    request_id llm-debug.jsonl` finds the full detail for one console line."""
    record = {"ts": time.time(), "request_id": request_id, **record}
    path = config.migration_dir / "llm-debug.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")
    return path


def run_tool_loop(
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    system: str,
    user: str,
    max_tool_calls: int,
    config: AgentConfig,
    model_name: str = "",
    phase: str = "",
) -> ToolLoopResult:
    """Run a bounded agentic loop: model <-> tools until no tool calls or cap hit."""
    tool_by_name: dict[str, BaseTool] = {t.name: t for t in tools}
    bound = model.bind_tools(list(tools)) if tools else model
    # Prompt caching (M6 Task 4): both the system prompt and the user
    # message's caller-assembled prefix (agents/compile_fix.py etc. put the
    # stable part -- inlined file contents -- first specifically so this
    # breakpoint lands after it) are marked cacheable. A model/provider that
    # ignores cache_control simply pays the normal price; this never changes
    # correctness, only cost.
    if config.prompt_caching_enabled:
        system_content: Any = _cache_marked_content(system)
        user_content: Any = _cache_marked_content(user)
    else:
        system_content, user_content = system, user
    messages: list[Any] = [SystemMessage(content=system_content), HumanMessage(content=user_content)]
    result = ToolLoopResult(final_text="")
    # "Round" = one run_tool_loop call, not one while-loop iteration: this
    # flag is set at most once for the whole call, so only the first budget
    # crossing ever prompts; every later crossing auto-compacts silently.
    asked_this_round = False

    request_id = uuid.uuid4().hex[:8]
    tag = f"[{phase or '?'}/{request_id}]"

    debug_path = _append_llm_debug(
        config, request_id, {"event": "start", "phase": phase, "model": model_name, "system": system, "user": user}
    )
    LOG.info(
        f"{tag} tool loop start: model={model_name or '?'} tools={[t.name for t in tools]} "
        f"max_tool_calls={max_tool_calls} full_detail={debug_path}"
    )

    while True:
        # Pre-flight compaction (M6 Task 12): the post-invoke check below
        # catches an overflow only after that request was already built,
        # sent, and billed. This estimate (char-count / 4, no tokenizer
        # dependency -- see _estimate_tokens) runs BEFORE bound.invoke, so a
        # transcript that's already over budget from the previous round's
        # growth gets compacted before paying for the oversized request
        # rather than after. Deliberately conservative: it only ever
        # triggers compaction earlier than the post-invoke check would have,
        # never instead of it -- the post-invoke check stays as the backstop
        # for whatever the char-count heuristic underestimates.
        estimated_tokens = _estimate_tokens(messages)
        if estimated_tokens > config.max_agent_context_tokens:
            do_compact = True
            if not config.headless:
                if not asked_this_round:
                    asked_this_round = True
                    decision = interrupt(
                        {
                            "reason": "context_budget_preflight",
                            "estimated_tokens": estimated_tokens,
                            "threshold": config.max_agent_context_tokens,
                        }
                    )
                    normalized = decision if isinstance(decision, str) else str(decision)
                    do_compact = normalized.strip().lower() != "continue"
            if do_compact:
                messages = compact_messages(messages, config)
                result.compactions += 1
                LOG.info(
                    "tool loop pre-flight compacted: estimated_tokens=%d threshold=%d",
                    estimated_tokens,
                    config.max_agent_context_tokens,
                )

        # M6 Task 12 tracing: tags/metadata on each model.invoke, gated
        # behind config.tracing_enabled so an untraced run pays zero cost
        # for building this dict. request_id (this tool loop's own
        # correlation id, already used to tag stdout/llm-debug.jsonl lines)
        # is what lets a LangSmith trace for one round be matched back to
        # the exact `grep request_id llm-debug.jsonl` line for its full
        # prompt/tool-call detail.
        invoke_kwargs: dict[str, Any] = {}
        if config.tracing_enabled:
            invoke_kwargs["config"] = {
                "tags": [f"phase:{phase or 'unknown'}", f"request_id:{request_id}"],
                "metadata": {"phase": phase, "request_id": request_id, "round": result.llm_requests + 1},
                "run_name": f"{phase or 'llm'}-round-{result.llm_requests + 1}",
            }
        ai: AIMessage = bound.invoke(messages, **invoke_kwargs)
        result.llm_requests += 1
        usage = getattr(ai, "usage_metadata", None) or {}
        current_input_tokens = usage.get("input_tokens", 0)
        current_output_tokens = usage.get("output_tokens", 0)
        result.input_tokens += current_input_tokens
        result.output_tokens += current_output_tokens
        # LangChain nests Anthropic-style cache token counts under
        # input_token_details -- absent entirely on a provider/model that
        # doesn't report them, which is fine: 0 is the correct "no signal"
        # value here, not an error (M6 Task 4).
        token_details = usage.get("input_token_details") or {}
        result.cache_read_input_tokens += token_details.get("cache_read", 0)
        result.cache_creation_input_tokens += token_details.get("cache_creation", 0)
        messages.append(ai)

        tool_calls = getattr(ai, "tool_calls", None) or []
        LOG.info(
            f"{tag} round {result.llm_requests}: input_tokens={current_input_tokens} "
            f"output_tokens={current_output_tokens} tool_calls_requested={len(tool_calls)}"
        )
        if not tool_calls:
            result.final_text = ai.content if isinstance(ai.content, str) else str(ai.content)
            LOG.info(f"{tag} finished: no more tool calls requested, llm_requests={result.llm_requests} tool_calls={result.tool_calls}")
            _append_llm_debug(config, request_id, {"event": "finish", "round": result.llm_requests, "final_text": result.final_text})
            break

        for call in tool_calls:
            result.tool_calls += 1
            name = call.get("name", "")
            args = call.get("args", {}) or {}
            # read_file/write_file/str_replace (tools/fs.py) all take a "path" arg --
            # surfacing it here answers "which file is being read/written" without
            # opening llm-debug.jsonl.
            path_arg = args.get("path", "") if isinstance(args, dict) else ""
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
            if output.startswith(MANUAL_REVIEW_PREFIX):
                result.manual_review_reason = output[len(MANUAL_REVIEW_PREFIX):]
            LOG.info(f"{tag} tool call {result.tool_calls}: name={name} path={path_arg or '-'} output_chars={len(output)}")
            _append_llm_debug(
                config,
                request_id,
                {"event": "tool_call", "round": result.llm_requests, "seq": result.tool_calls, "name": name, "args": args, "output": output},
            )
            messages.append(ToolMessage(content=output, tool_call_id=call.get("id", "")))

        if result.manual_review_reason is not None:
            LOG.info(f"{tag} stopped: agent flagged for manual review: {result.manual_review_reason!r}")
            _append_llm_debug(
                config, request_id, {"event": "manual_review_requested", "round": result.llm_requests, "reason": result.manual_review_reason}
            )
            break

        if result.tool_calls >= max_tool_calls:
            result.stopped_by_cap = True
            LOG.warning(
                f"{tag} stopped: cap of {max_tool_calls} tool calls reached "
                f"(llm_requests={result.llm_requests}, input_tokens={result.input_tokens}, output_tokens={result.output_tokens})"
            )
            _append_llm_debug(config, request_id, {"event": "cap_reached", "round": result.llm_requests, "tool_calls": result.tool_calls})
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
    if model_name:
        from . import pricing

        result.total_cost_usd = pricing.cost_usd(
            model_name,
            result.input_tokens,
            result.output_tokens,
            cache_read_input_tokens=result.cache_read_input_tokens,
        )
    else:
        result.total_cost_usd = None
    return result


UsageWriter = Callable[[dict[str, Any]], None]


def append_usage_log(config: AgentConfig, record: dict[str, Any]) -> None:
    """Append one usage record to <spring-repo>/.migration/llm-usage.json (JSONL).

    M6 Task 5: attaches input_cost_usd/output_cost_usd/total_cost_usd here,
    the single place every call site's record passes through, rather than at
    each of the 6 append_usage_log call sites. Silently skipped (fields
    simply absent) when the record has no "model"/"input_tokens" -- some
    callers (compact_messages) may omit output_tokens; cost is None-safe.
    """
    from . import pricing

    model_id = record.get("model")
    input_tokens = record.get("input_tokens")
    output_tokens = record.get("output_tokens")
    if model_id and isinstance(input_tokens, int) and isinstance(output_tokens, int):
        cache_read = record.get("cache_read_input_tokens", 0) or 0
        cost = pricing.cost_usd(model_id, input_tokens, output_tokens, cache_read_input_tokens=cache_read)
        if cost is not None:
            record = {**record, "total_cost_usd": round(cost, 6)}

    path = config.migration_dir / "llm-usage.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")
