"""Architect agent (M6 Task 6): runs once, after inventory and before the
first transform, and writes <spring-repo>/.migration/decisions.md.

This is the cross-phase memory the engine was otherwise missing entirely --
each run_tool_loop call starts a fresh `messages` list, so without this file
the routes agent knows nothing of what compile-fix (or any earlier phase)
decided. Every later agent prompt is told the path to this file, never its
contents (the push/pull discipline: push paths and IDs, let the agent pull
off disk) -- see nodes/architect.py and the read-decisions-first line added
to each of the other four agents' prompts.

Standalone prompt, not PromptBuilder (scripts/prompt_builder.py): that class
is shared with the legacy cursor-agent engine and deliberately untouched by
langgraph-only additions (same rationale as agents/compile_fix.py's own
_inline_affected_files).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from ..config import AgentConfig
from ..llm import ToolLoopResult, append_usage_log, make_model, run_tool_loop
from ..tools.fs import FsJail

LOG = logging.getLogger("agent.architect")

DECISIONS_RELATIVE_PATH = ".migration/decisions.md"

# M6 Task 6 push/pull discipline: every later agent's prompt gets the PATH to
# decisions.md, never its contents pushed inline -- the agent pulls it off
# disk itself, via read_file, only if/when it needs it. Appended verbatim to
# each of the 4 other agents' SYSTEM_PROMPT constants (compile_fix, routes,
# config_mapping, runtime_wiring).
DECISIONS_READ_FIRST_LINE = (
    f"\nBefore making changes, read {DECISIONS_RELATIVE_PATH} if it exists -- "
    "it records the architect's dependency map, config-mapping policy, async "
    "policy, and no_migration list for this run. Follow those decisions "
    "rather than improvising your own."
)

_SYSTEM_PROMPT = """You are the architect phase of a Play-to-Spring-Boot migration.
Your job is to make the cross-cutting decisions that every later phase depends on,
BEFORE any source code is transformed, and write them to one file:
.migration/decisions.md (relative to the Spring repo root).

You do not migrate code yourself. You decide:
- Dependency map: for each notable build.sbt/Play dependency, its Spring/Maven counterpart
  (or "no equivalent -- flag for manual redesign").
- Config map: how Play's conf/application.conf keys should map to Spring's
  application.properties, at a policy level (not every single key -- the
  config-mapping phase handles the mechanical translation later).
- Async policy: how Play's asynchronous idioms (CompletionStage, Akka actors,
  reactive streams) should be translated -- @Async, WebFlux, or something
  else. This decision repeats in every layer if it's wrong, so get it right
  here rather than improvising it per-file later.
- no_migration list: Play-only glue with no Spring equivalent that should be
  left out of the migration entirely (e.g. a Module.java DI binder file) --
  without this list, such files show up as a permanent shortfall on every
  later completeness check.
- Open concerns: anything you're not confident about, for a human to review.

Write the file using the write_file tool. Use this structure:

# Migration Decisions

## Dependency map
| Play/SBT dependency | Spring/Maven equivalent | Notes |

## Config map policy
(prose, plus a short table of any non-obvious key mappings)

## Async policy
(prose: the chosen approach and why)

## no_migration
- <file or pattern>: <reason>

## Concerns
- <anything uncertain, for human review>

Base every decision on the actual pre-flight scan data given to you below --
do not invent dependencies or config keys that aren't present in the scan.
When you are done, call write_file once with the complete file content, then
stop."""


def _format_source_inventory(source_inventory: dict[str, Any] | None) -> str:
    if not source_inventory:
        return "(no Play source inventory available)"
    by_layer = source_inventory.get("by_layer") or {}
    lines = [f"Total Java files: {source_inventory.get('total_java_files', 0)}", "By layer:"]
    for layer, count in by_layer.items():
        lines.append(f"  {layer}: {count}")
    return "\n".join(lines)


def _format_play_surface_inventory(play_surface_inventory: dict[str, Any] | None) -> str:
    if not play_surface_inventory:
        return "(no play-surface scan available -- no play_repo/jar configured)"
    coverage = play_surface_inventory.get("coveragePercent", 0.0) or 0.0
    unknown = play_surface_inventory.get("unknownCount", 0) or 0
    paradigm = play_surface_inventory.get("paradigmCount", 0) or 0
    lines = [f"Coverage: {coverage:.1f}%, unknown constructs: {unknown}, paradigm mismatches: {paradigm}"]
    for t in play_surface_inventory.get("touchpoints", []) or []:
        if t.get("classification") in ("PARADIGM", "UNKNOWN"):
            lines.append(f"  [{t.get('classification')}] {t.get('construct')} @ {t.get('location')}")
    return "\n".join(lines)


def build_architect_prompt(
    source_inventory: dict[str, Any] | None,
    play_surface_inventory: dict[str, Any] | None,
) -> str:
    return (
        "Pre-flight scan data for this migration:\n\n"
        "## Source inventory (file counts by layer)\n"
        f"{_format_source_inventory(source_inventory)}\n\n"
        "## Play-surface scan (framework API touchpoints, KNOWN/UNKNOWN/PARADIGM)\n"
        f"{_format_play_surface_inventory(play_surface_inventory)}\n\n"
        f"Write your decisions to {DECISIONS_RELATIVE_PATH} now."
    )


def run_architect(
    config: AgentConfig,
    source_inventory: dict[str, Any] | None,
    play_surface_inventory: dict[str, Any] | None,
    attempt: int,
    model_override: Any = None,
) -> tuple[list[Path], ToolLoopResult]:
    """One architect attempt. Returns (edited files, loop result)."""
    prompt = build_architect_prompt(source_inventory, play_surface_inventory)

    # Premium tier, one-shot -- same rationale as bootstrap: this is a
    # high-stakes, run-wide decision, not a per-slice fix round, so there's
    # no cheap-first-pass economics to exploit here.
    model_name = config.model_premium
    model = model_override if model_override is not None else make_model(config, model_name)

    jail = FsJail(config.spring_repo, config.play_repo, dry_run=config.dry_run)
    started = time.time()
    result = run_tool_loop(
        model=model,
        tools=jail.build_tools(phase="architect"),
        system=_SYSTEM_PROMPT,
        user=prompt,
        max_tool_calls=config.max_agent_tool_calls_for("architect") * 2,
        config=config,
        model_name=model_name,
        phase="architect",
    )
    append_usage_log(
        config,
        {
            "ts": started,
            "phase": "architect",
            "attempt": attempt,
            "model": model_name,
            "llm_requests": result.llm_requests,
            "tool_calls": result.tool_calls,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "cache_read_input_tokens": result.cache_read_input_tokens,
            "compactions": result.compactions,
            "edited_files": [str(p) for p in jail.edited_files],
            "total_cost_usd": result.total_cost_usd,
        },
    )
    LOG.info("architect attempt %d done: edits=%d tool_calls=%d", attempt, len(jail.edited_files), result.tool_calls)
    return jail.edited_files, result
