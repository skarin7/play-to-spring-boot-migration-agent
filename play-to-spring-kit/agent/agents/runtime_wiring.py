"""Runtime-wiring agent: fixes Spring Boot startup failures (bean wiring, config).

``verify_node``/the compile-fix subgraph only prove the code *compiles* --
missing beans, bad `@ConditionalOnExpression` gaps, and other runtime-only
wiring failures (see docs/compile-fixes-for-toolkit.md's "Spring Boot
runtime" table) are invisible until `mvn spring-boot:run` is actually
attempted (agent/tools/maven.py). Model tier is picked by
config.choose_model: escalates to premium on repeated retries or a boot log
with several distinct "Caused by:" failures (see docs/superpowers/specs/
2026-07-27-heuristic-model-router-design.md).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from ..config import AgentConfig, TaskSignals
from ..llm import ToolLoopResult, append_usage_log, make_model, run_tool_loop
from ..tools.fs import FsJail
from ..tools.setup_ops import kit_root
from .architect import DECISIONS_READ_FIRST_LINE

LOG = logging.getLogger("agent.runtime_wiring")

_REFERENCE_HEADING = "## Spring Boot runtime (`mvn spring-boot:run`) — wiring and config"
_NEXT_HEADING_PREFIX = "\n## "

SYSTEM_PROMPT = """\
You are a Spring Boot migration assistant. The application fails to start \
(it never reaches the "Started ... in N seconds" line). Your only job is to \
fix the runtime wiring/config problem shown in the boot log below -- missing \
or misconfigured beans, bad @ConditionalOnExpression, null configuration \
values, and similar startup-time failures. Do not touch unrelated code.

Use the read_file/list_dir tools to find the failing class, then \
str_replace or write_file to fix it. A reference table of previously-seen \
symptoms and fixes for this exact class of problem follows -- use it when \
the current failure matches a known pattern:

{reference}
""" + DECISIONS_READ_FIRST_LINE


def _reference_slice(markdown: str) -> str | None:
    i = markdown.find(_REFERENCE_HEADING)
    if i == -1:
        return None
    j = markdown.find(_NEXT_HEADING_PREFIX, i + len(_REFERENCE_HEADING))
    if j == -1:
        return markdown[i:].strip()
    return markdown[i:j].strip()


def load_runtime_wiring_reference() -> str:
    """Load just the "Spring Boot runtime" table from the repo-root reference doc.

    Mirrors bootstrap.py's load_builder_skill_step1_markdown/
    extract_builder_skill_step1_only heading-slice technique: never raise,
    fall back to a short placeholder if the doc (or the section) is missing.
    """
    path = kit_root().parent / "docs" / "compile-fixes-for-toolkit.md"
    if not path.is_file():
        return f"# Missing runtime-wiring reference doc (expected {path})\n"
    full = path.read_text(encoding="utf-8", errors="replace")
    chunk = _reference_slice(full)
    if chunk:
        return chunk
    LOG.warning("Reference doc at %s missing %r section; using full file.", path, _REFERENCE_HEADING)
    return full.strip()


def _user_prompt(boot_log_tail: str) -> str:
    return (
        "The Spring Boot application failed to start within the timeout. "
        "Here is the tail of its boot log:\n\n"
        f"{boot_log_tail}\n\n"
        "Diagnose and fix the wiring/config problem so the application starts."
    )


def _caused_by_count(boot_log_tail: str) -> int:
    return max(1, boot_log_tail.count("Caused by:"))


def run_runtime_wiring_agent(
    config: AgentConfig,
    boot_log_tail: str,
    attempt: int,
    model_override: Any = None,
) -> tuple[list[Path], ToolLoopResult]:
    """One runtime-wiring round. Returns (edited files, loop result)."""
    model_name = config.choose_model(
        TaskSignals(retry_count=attempt - 1, item_count=_caused_by_count(boot_log_tail))
    )
    model = model_override if model_override is not None else make_model(config, model_name)

    jail = FsJail(config.spring_repo, config.play_repo, dry_run=config.dry_run)
    started = time.time()
    system = SYSTEM_PROMPT.format(reference=load_runtime_wiring_reference())
    result = run_tool_loop(
        model=model,
        tools=jail.build_tools(phase="runtime_wiring"),
        system=system,
        user=_user_prompt(boot_log_tail),
        max_tool_calls=config.max_agent_tool_calls_for("runtime_wiring"),
        config=config,
        model_name=model_name,
        phase="runtime_wiring",
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
            "cache_read_input_tokens": result.cache_read_input_tokens,
            "compactions": result.compactions,
            "edited_files": [str(p) for p in jail.edited_files],
        },
    )
    LOG.info(
        "runtime_wiring attempt %d done: model=%s edits=%d tool_calls=%d",
        attempt,
        model_name,
        len(jail.edited_files),
        result.tool_calls,
    )
    return jail.edited_files, result
