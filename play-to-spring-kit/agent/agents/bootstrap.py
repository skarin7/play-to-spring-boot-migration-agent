"""Bootstrap agent: creates pom.xml / Application.java / application.properties.

Replaces the legacy cursor-agent init call (migration_orchestrator.py
:load_builder_skill_step1_markdown / :bootstrap_initialize_via_cursor_agent).
Runs at the premium tier (this is a one-shot, high-stakes scaffold — no cheap
first pass like the per-slice compile-fix agent). Verification is deterministic
and happens in the caller (graph.py bootstrap_verify_node: do the three files
exist on disk?), never self-reported by the LLM.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from ..config import AgentConfig
from ..llm import ToolLoopResult, append_usage_log, make_model, run_tool_loop
from ..tools.fs import FsJail
from ..tools.setup_ops import kit_root

LOG = logging.getLogger("agent.bootstrap")

_STEP1_HEADING = "## Step 1: Initialize Spring project"
_STEP2_HEADING = "\n## Step 2:"


def extract_builder_skill_step1_only(skill_markdown: str) -> str | None:
    """Return only the Initialize section from the builder skill markdown."""
    i = skill_markdown.find(_STEP1_HEADING)
    if i == -1:
        return None
    j = skill_markdown.find(_STEP2_HEADING, i + len(_STEP1_HEADING))
    if j == -1:
        return skill_markdown[i:].strip()
    return skill_markdown[i:j].strip()


def load_builder_skill_step1_markdown(play_repo: Path | None) -> str:
    """
    Load the builder skill from the play repo or kit, but only Step 1 (init).

    Inlining the full orchestrator skill used to make agents follow
    migrate-app / per-layer instructions and never finish init.
    """
    candidates = []
    if play_repo is not None:
        candidates.append(play_repo / ".cursor/skills/play-spring-builder/SKILL.md")
    candidates.append(kit_root() / "skills/builder-skill.md")
    for p in candidates:
        if not p.is_file():
            continue
        full = p.read_text(encoding="utf-8", errors="replace")
        chunk = extract_builder_skill_step1_only(full)
        if chunk:
            return chunk
        LOG.warning("Builder skill at %s missing %r section; using full file.", p, _STEP1_HEADING)
        return full.strip()
    return f"# Missing builder skill. Run setup.sh.\n# Expected one of: {candidates}\n"


def run_bootstrap(
    config: AgentConfig,
    attempt: int,
    model_override: Any = None,
) -> tuple[list[Path], ToolLoopResult]:
    """One bootstrap attempt. Returns (edited files, loop result)."""
    from prompt_builder import PromptBuilder

    builder = PromptBuilder(
        spring_repo=config.spring_repo,
        play_repo=config.play_repo or config.spring_repo,
        status_path=config.status_path,
    )
    step1_md = load_builder_skill_step1_markdown(config.play_repo)
    prompt = builder.bootstrap_prompt(step1_md=step1_md)

    model = model_override if model_override is not None else make_model(config, config.model_premium)

    jail = FsJail(config.spring_repo, config.play_repo)
    started = time.time()
    result = run_tool_loop(
        model=model,
        tools=jail.build_tools(),
        system=builder.system_prompt(),
        user=prompt,
        max_tool_calls=config.max_agent_tool_calls * 2,  # scaffolding 3 files needs more room than a fix round
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
            "edited_files": [str(p) for p in jail.edited_files],
        },
    )
    LOG.info("bootstrap attempt %d done: edits=%d tool_calls=%d", attempt, len(jail.edited_files), result.tool_calls)
    return jail.edited_files, result
