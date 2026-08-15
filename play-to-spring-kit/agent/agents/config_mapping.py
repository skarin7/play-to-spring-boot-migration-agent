"""Config-mapping agent: adds Spring-idiomatic keys for leftover Play config keys.

``scripts/conf_to_application_properties.py`` already flattens Play's
``conf/application.conf`` into ``<spring_repo>/src/main/resources/application.properties``
verbatim (dot-keys, e.g. ``mongodb.uri=...``) but never renames them to
Spring's own idiomatic property names (e.g. ``spring.data.mongodb.uri``).
``tools/config_mapping.py``'s seed table already resolves the well-known
cases deterministically; this agent handles everything else. Model tier is
picked by config.choose_model: escalates to premium on repeated retries or a
large leftover-key count (see docs/superpowers/specs/
2026-07-27-heuristic-model-router-design.md).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from ..config import AgentConfig, TaskSignals
from ..llm import ToolLoopResult, append_usage_log, make_model, run_tool_loop
from ..tools.config_mapping import SEED_KEY_MAP
from ..tools.fs import FsJail
from .architect import DECISIONS_READ_FIRST_LINE

LOG = logging.getLogger("agent.config_mapping")

SYSTEM_PROMPT_TEMPLATE = """\
You are a Spring Boot migration assistant. Your only job is to add \
Spring-idiomatic equivalents for Play/HOCON-derived configuration keys that \
have already been copied verbatim into src/main/resources/application.properties.

A seed table of well-known Play key -> Spring canonical key mappings has \
already been applied automatically; use it as a reference to recognize \
near-misses (e.g. a differently-spelled variant of a key already in the \
table) and to keep any new mappings you add in the same idiomatic style:
{seed_table}

For each leftover key below, decide whether it has a well-known Spring \
canonical property name and, if so, append that key=value line to \
src/main/resources/application.properties using the read_file/write_file/ \
str_replace tools -- append, do not remove or rewrite the existing verbatim \
key, since other code may still depend on it. If a key has no well-known \
Spring equivalent, leave it alone.
""" + DECISIONS_READ_FIRST_LINE


def _format_leftover(leftover: dict[str, Any]) -> str:
    return "\n".join(f"- {k}={v}" for k, v in leftover.items())


def _format_seed_table(seed_map: dict[str, str]) -> str:
    return "\n".join(f"- {k} -> {v}" for k, v in seed_map.items())


def _user_prompt(leftover: dict[str, Any]) -> str:
    return (
        "The following config keys have no known Spring canonical mapping yet:\n\n"
        f"{_format_leftover(leftover)}\n\n"
        "Add Spring-idiomatic equivalents to application.properties where you can."
    )


def run_config_mapping_agent(
    config: AgentConfig,
    leftover: dict[str, Any],
    attempt: int,
    model_override: Any = None,
) -> tuple[list[Path], ToolLoopResult]:
    """One config-mapping round. Returns (edited files, loop result)."""
    model_name = config.choose_model(TaskSignals(retry_count=attempt - 1, item_count=len(leftover)))
    model = model_override if model_override is not None else make_model(config, model_name)

    jail = FsJail(config.spring_repo, config.play_repo, dry_run=config.dry_run)
    started = time.time()
    system = SYSTEM_PROMPT_TEMPLATE.format(seed_table=_format_seed_table(SEED_KEY_MAP))
    result = run_tool_loop(
        model=model,
        tools=jail.build_tools(phase="config_mapping"),
        system=system,
        user=_user_prompt(leftover),
        max_tool_calls=config.max_agent_tool_calls_for("config_mapping"),
        config=config,
        model_name=model_name,
        phase="config_mapping",
    )
    append_usage_log(
        config,
        {
            "ts": started,
            "phase": "config_mapping",
            "attempt": attempt,
            "model": model_name,
            "leftover_count": len(leftover),
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
        "config_mapping attempt %d done: leftover=%d edits=%d tool_calls=%d",
        attempt,
        len(leftover),
        len(jail.edited_files),
        result.tool_calls,
    )
    return jail.edited_files, result
