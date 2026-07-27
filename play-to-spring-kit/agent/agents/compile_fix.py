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

from ..config import AgentConfig
from ..llm import ToolLoopResult, append_usage_log, make_model, run_tool_loop
from ..tools.fs import FsJail

LOG = logging.getLogger("agent.compile_fix")


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
    system = builder.system_prompt()
    user = builder.fix_prompt(clusters, config.spring_repo)

    model_name = config.model_for_retry(retry_count)
    model = model_override if model_override is not None else make_model(config, model_name)

    jail = FsJail(config.spring_repo, config.play_repo)
    started = time.time()
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
    LOG.info(
        "compile-fix round done: model=%s edits=%d tool_calls=%d",
        model_name,
        len(jail.edited_files),
        result.tool_calls,
    )
    return jail.edited_files, result
