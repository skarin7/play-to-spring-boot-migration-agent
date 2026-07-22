"""Routes agent: annotates Spring controller methods with ``@*Mapping`` routes.

Play ``conf/routes`` maps HTTP routes to controller methods; the toolkit JAR
migrates the method bodies but never adds the Spring routing annotations
(docs/play_to_spring_migration.md 6.4/7.1: "routes | @RestController +
@*Mapping"). This is low-stakes, mechanical annotation work — cheap tier
only, no escalation (unlike the compile-fix agent's two-tier retry ladder).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from ..config import AgentConfig
from ..llm import ToolLoopResult, append_usage_log, make_model, run_tool_loop
from ..tools.fs import FsJail

LOG = logging.getLogger("agent.routes")

SYSTEM_PROMPT = """\
You are a Spring Boot migration assistant. Your only job is to add the \
correct routing annotations to Spring controller methods so they match a \
list of Play framework routes.

For each unmapped route, find the corresponding method in the Spring \
controller (converted from the Play controller of the same simple class \
name) and add the appropriate annotation immediately above it:
  GET -> @GetMapping("<path>")
  POST -> @PostMapping("<path>")
  PUT -> @PutMapping("<path>")
  DELETE -> @DeleteMapping("<path>")
  PATCH -> @PatchMapping("<path>")
Play path parameters like ":id" become Spring path variables "{id}" — update \
both the annotation path and add @PathVariable to the matching method \
parameter if it is missing. Make sure the containing class is annotated \
@RestController. Use the read_file/list_dir tools to find the controller, \
then str_replace or write_file to add the annotation. Do not change method \
bodies or business logic.
"""


def _format_unmapped(unmapped: list[dict[str, Any]]) -> str:
    lines = [f"- {r['method']} {r['path']} -> {r['controller']}.{r['action']}({r['params']})" for r in unmapped]
    return "\n".join(lines)


def _user_prompt(unmapped: list[dict[str, Any]]) -> str:
    return (
        "The following Play routes have no matching Spring @*Mapping annotation yet:\n\n"
        f"{_format_unmapped(unmapped)}\n\n"
        "Add the missing annotations to the corresponding Spring controller methods."
    )


def run_routes_agent(
    config: AgentConfig,
    unmapped: list[dict[str, Any]],
    attempt: int,
    model_override: Any = None,
) -> tuple[list[Path], ToolLoopResult]:
    """One routes-mapping round. Returns (edited files, loop result)."""
    model = model_override if model_override is not None else make_model(config, config.model_cheap)

    jail = FsJail(config.spring_repo, config.play_repo)
    started = time.time()
    result = run_tool_loop(
        model=model,
        tools=jail.build_tools(),
        system=SYSTEM_PROMPT,
        user=_user_prompt(unmapped),
        max_tool_calls=config.max_agent_tool_calls,
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
            "edited_files": [str(p) for p in jail.edited_files],
        },
    )
    LOG.info(
        "routes attempt %d done: unmapped=%d edits=%d tool_calls=%d",
        attempt,
        len(unmapped),
        len(jail.edited_files),
        result.tool_calls,
    )
    return jail.edited_files, result
