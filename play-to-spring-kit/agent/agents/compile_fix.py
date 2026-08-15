"""Compile-fix agent: LLM edits Spring sources to resolve clustered errors.

Prompts come from the legacy PromptBuilder (token-budgeted); the agent only
gets path-jailed fs tools. It never compiles — verification is the next
compile node in the graph.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from ..config import AgentConfig, TaskSignals
from ..llm import ToolLoopResult, append_usage_log, make_model, run_tool_loop
from ..tools.fs import FsJail
from .architect import DECISIONS_READ_FIRST_LINE

LOG = logging.getLogger("agent.compile_fix")

_MAX_INLINE_FILES = 5
_MAX_INLINE_FILE_CHARS = 8000


def _inline_affected_files(spring_repo: Path, clusters: list[Any]) -> str:
    """Read the files these clusters point to and inline their current
    content in the prompt, so the agent doesn't spend its first N tool calls
    just re-reading files it already knows -- from the cluster data -- it
    needs to fix. Confined to compile_fix.py (langgraph-only): prompt_builder.py
    is shared with the legacy cursor-agent engine and its fix_prompt() is
    deliberately token-budgeted for that engine's own native file access, so
    it's left untouched here."""
    spring_repo = spring_repo.resolve()
    paths: list[Path] = []
    seen: set[Path] = set()
    for c in clusters:
        candidates = [c.representative.get("file")] + list(c.affected_files or [])
        for raw in candidates:
            if not raw or len(paths) >= _MAX_INLINE_FILES:
                continue
            p = Path(raw)
            if not p.is_absolute():
                p = spring_repo / raw
            try:
                p = p.resolve()
            except OSError:
                continue
            if p in seen:
                continue
            seen.add(p)
            try:
                p.relative_to(spring_repo)
            except ValueError:
                continue  # outside the spring repo -- don't inline
            paths.append(p)

    sections = []
    for p in paths:
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if len(content) > _MAX_INLINE_FILE_CHARS:
            content = content[:_MAX_INLINE_FILE_CHARS] + "\n... (truncated)"
        sections.append(f"--- current content of {p} ---\n{content}")

    if not sections:
        return ""
    return "\n\nCurrent file contents (already read for you -- no need to read_file these again):\n\n" + "\n\n".join(
        sections
    )


def _clusters_from_dicts(cluster_dicts: list[dict[str, Any]]):
    from error_clusterer import ErrorCluster  # scripts/ on sys.path via package __init__

    return [
        ErrorCluster(
            root_cause=d.get("root_cause", ""),
            representative=d.get("representative", {}),
            affected_files=d.get("affected_files", []),
            count=d.get("count", 1),
            suggested_fix=d.get("suggested_fix"),
        )
        for d in cluster_dicts
    ]


def run_compile_fix(
    config: AgentConfig,
    cluster_dicts: list[dict[str, Any]],
    retry_count: int,
    slice_id: str,
    model_override=None,
) -> tuple[list[Path], ToolLoopResult]:
    """One LLM fix round. Returns (edited files, loop result)."""
    from prompt_builder import PromptBuilder

    builder = PromptBuilder(
        spring_repo=config.spring_repo,
        play_repo=config.play_repo or config.spring_repo,
        status_path=config.status_path,
    )
    clusters = _clusters_from_dicts(cluster_dicts)
    # Static text appended after PromptBuilder's own system prompt -- stays
    # stable across every fix round in a slice, so it doesn't disturb the
    # cache_control prefix (M6 Task 4/6).
    system = builder.system_prompt() + DECISIONS_READ_FIRST_LINE
    # Inlined file contents first, cluster/error text last (M6 Task 4): the
    # file contents are the stable part across a slice's fix rounds -- the
    # same files, re-read every round -- while the error clusters are what
    # actually changes round to round. Prompt caching only pays off on a
    # prefix that's identical across requests; putting the volatile part
    # first (the old order) defeated caching on every single round.
    user = _inline_affected_files(config.spring_repo, clusters) + "\n\n" + builder.fix_prompt(clusters, config.spring_repo)

    model_name = config.choose_model(TaskSignals(retry_count=retry_count, item_count=len(clusters)))
    model = model_override if model_override is not None else make_model(config, model_name)

    jail = FsJail(config.spring_repo, config.play_repo, dry_run=config.dry_run)
    started = time.time()
    result = run_tool_loop(
        model=model,
        tools=jail.build_tools(phase="compile_fix"),
        system=system,
        user=user,
        max_tool_calls=config.max_agent_tool_calls_for("compile_fix"),
        config=config,
        model_name=model_name,
        phase="compile_fix",
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
            "cache_read_input_tokens": result.cache_read_input_tokens,
            "compactions": result.compactions,
            "edited_files": [str(p) for p in jail.edited_files],
        },
    )
    LOG.info(
        "compile-fix round done: model=%s edits=%d tool_calls=%d",
        model_name,
        len(jail.edited_files),
        result.tool_calls,
    )
    return jail.edited_files, result
