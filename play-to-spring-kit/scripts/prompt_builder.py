#!/usr/bin/env python3
"""
Minimal, structured prompt construction for cursor-agent.

Separates static system context (sent once / cached) from per-call dynamic
content (errors only). Token budgets:
  system_prompt()    ≤ 200 tokens
  fix_prompt()       ≤ 500 tokens  (for up to 5 clusters)
  bootstrap_prompt() ≤ 1000 tokens
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from error_clusterer import ErrorCluster

_MAX_CLUSTERS = 5


class PromptBuilder:
    """
    Constructs minimal prompts for cursor-agent.

    Instantiate once per orchestrator run; pass spring_repo / play_repo / status_path
    at construction time so system_prompt() is stable across calls.
    """

    def __init__(self, spring_repo: Path, play_repo: Path, status_path: Path) -> None:
        self._spring = spring_repo
        self._play = play_repo
        self._status = status_path

    # ------------------------------------------------------------------
    # system_prompt — static context, ≤200 tokens, sent once per session
    # ------------------------------------------------------------------

    def system_prompt(self) -> str:
        """
        Static context: role, absolute paths, forbidden actions.
        Target: ≤200 tokens.
        """
        return (
            f"You are a Java Spring Boot migration assistant.\n"
            f"Spring repo: {self._spring}\n"
            f"Play repo: {self._play} (READ ONLY — never modify)\n"
            f"State file: {self._status}\n"
            f"\n"
            f"Rules:\n"
            f"- Edit only {self._spring}/src/main/java/**\n"
            f"- Preserve business logic verbatim\n"
            f"- Use Spring 6 / Jakarta EE APIs (not javax.*)\n"
            f"- Do not run migrate-app or any dev-toolkit commands\n"
        )

    # ------------------------------------------------------------------
    # fix_prompt — per-call, ≤500 tokens for ≤5 clusters
    # ------------------------------------------------------------------

    def fix_prompt(self, clusters: "list[ErrorCluster]", spring_repo: Path) -> str:
        """
        Per-call prompt: clustered errors only, no repeated boilerplate.
        Target: ≤500 tokens for up to 5 clusters.
        """
        top = clusters[:_MAX_CLUSTERS]
        lines = [f"Fix these Spring compile errors in {spring_repo.name}:", ""]

        for c in top:
            rep = c.representative
            lines.append(f"## {c.root_cause} ({c.count} occurrence{'s' if c.count != 1 else ''})")
            lines.append(f"File: {rep.get('file', '?')}:{rep.get('line', '?')}")
            lines.append(f"Error: {rep.get('message', '?')}")
            if c.suggested_fix:
                lines.append(f"Hint: {c.suggested_fix}")
            if c.count > 1 and len(c.affected_files) > 1:
                extras = c.affected_files[1:3]
                lines.append(f"Also affects: {', '.join(extras)}")
            lines.append("")

        lines.append(f"Edit only files under {spring_repo}/src/main/java/")
        lines.append("Do not modify Play source files.")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # bootstrap_prompt — init only, ≤1000 tokens total
    # ------------------------------------------------------------------

    def bootstrap_prompt(self, step1_md: str) -> str:
        """
        Init-only prompt. Inlines step1_md once; no migrate-app instructions.
        Target: ≤1000 tokens total.
        """
        return (
            f"You are invoked by migration_orchestrator.py (headless, non-interactive).\n"
            f"\n"
            f"## Task — bootstrap / init ONLY (then STOP)\n"
            f"\n"
            f"Complete Step 1: Initialize Spring project and finish.\n"
            f"Do not run migrate-app or any full migration steps.\n"
            f"\n"
            f"Paths:\n"
            f"- Spring repo: {self._spring}\n"
            f"- Play repo: {self._play} (read-only)\n"
            f"- State file: {self._status}\n"
            f"\n"
            f"FORBIDDEN: migrate-app, --layer, --path-prefix, Step 2/3 loops\n"
            f"\n"
            f"## Reference: Step 1 only\n"
            f"{step1_md}\n"
        )
