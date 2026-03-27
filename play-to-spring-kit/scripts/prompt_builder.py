#!/usr/bin/env python3
"""
Minimal, structured prompt builder for cursor-agent.

Separates static system context (sent once / cached) from per-call dynamic
content (errors only). Target: ~70% token reduction vs current approach.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from error_clusterer import ErrorCluster


class PromptBuilder:
    """
    Constructs minimal, structured prompts for cursor-agent.

    Token budget targets:
    - system_prompt(): ≤200 tokens
    - fix_prompt(): ≤500 tokens for typical 5-cluster input
    - bootstrap_prompt(): ≤1000 tokens total
    """

    MAX_CLUSTERS = 5

    def system_prompt(
        self, spring_repo: Path, play_repo: Path, status_path: Path
    ) -> str:
        """~200 token static context: role, absolute paths, forbidden actions."""
        return (
            "You are a Java Spring Boot migration assistant.\n"
            f"Spring repo: {spring_repo}\n"
            f"Play repo: {play_repo} (READ ONLY — never modify)\n"
            f"State file: {status_path}\n"
            "\n"
            "Rules:\n"
            f"- Edit only {spring_repo}/src/main/java/**\n"
            "- Preserve business logic verbatim\n"
            "- Use Spring 6 / Jakarta EE APIs (not javax.*)\n"
            "- Do not run migrate-app or any dev-toolkit commands"
        )

    def fix_prompt(self, clusters: list[ErrorCluster], spring_repo: Path) -> str:
        """
        Per-call prompt: only the clustered errors + affected file snippets.
        No repeated boilerplate. Target: <500 tokens for typical fix round.
        """
        lines: list[str] = []
        lines.append(f"Fix these Spring compile errors in {spring_repo.name}:")
        lines.append("")

        for cluster in clusters[: self.MAX_CLUSTERS]:
            lines.append(f"## {cluster.root_cause} ({cluster.count} occurrences)")
            rep = cluster.representative
            lines.append(f"File: {rep.get('file', '?')}:{rep.get('line', '?')}")
            lines.append(f"Error: {rep.get('message', '?')}")
            if cluster.suggested_fix:
                lines.append(f"Hint: {cluster.suggested_fix}")
            if len(cluster.affected_files) > 1:
                others = cluster.affected_files[1:4]  # show up to 3 additional files
                lines.append(f"Also affects: {', '.join(others)}")
            lines.append("")

        lines.append(f"Edit only files under {spring_repo}/src/main/java/")
        lines.append("Do not modify Play source files.")

        return "\n".join(lines)

    def bootstrap_prompt(
        self,
        play_repo: Path,
        spring_repo: Path,
        status_path: Path,
        step1_md: str,
    ) -> str:
        """Init-only prompt. Inlines step1_md once; no migrate-app instructions."""
        return (
            f"Bootstrap the Spring project at {spring_repo}.\n"
            f"Play repo (read-only): {play_repo}\n"
            f"State file: {status_path}\n"
            "\n"
            "Follow these steps exactly:\n"
            f"{step1_md}\n"
            "\n"
            "FORBIDDEN: regenerate pom.xml from scratch, run migrate-app, "
            "modify Play source files."
        )
