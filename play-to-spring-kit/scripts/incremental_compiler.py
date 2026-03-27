#!/usr/bin/env python3
"""
Incremental compiler: wraps ``mvn compile`` with optional module targeting.

Replaces full-project ``mvn compile`` with targeted compilation of only the
changed module, reducing compile time on multi-module projects.
"""

from __future__ import annotations

import logging
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

LOG = logging.getLogger("incremental_compiler")

# Maven error pattern: [ERROR] /path/File.java:[10,20] error: message
MVN_ERROR_RE = re.compile(
    r"\[ERROR\]\s+(.+\.java):\[(\d+),[^\]]+\]\s*(.+)",
)


@dataclass
class CompileResult:
    """Result of a Maven compilation."""

    returncode: int = 0
    log: str = ""
    errors: list[dict] = field(default_factory=list)
    duration_sec: float = 0.0


class IncrementalCompiler:
    """
    Wraps ``mvn compile`` with optional module targeting.

    Strategy:
    - Single-module projects: always ``mvn -q compile``
    - Multi-module: ``mvn -q compile -pl :<module> --am``
    - After LLM edits: full compile once for cross-module regressions

    Invariants:
    - ``returncode == 0`` iff ``errors`` is empty
    - ``duration_sec > 0``
    """

    def __init__(self, spring_repo: Path, dry_run: bool = False) -> None:
        self.spring_repo = spring_repo.resolve()
        self.dry_run = dry_run
        self._module_cache: dict[str, str | None] = {}  # dir -> artifactId or None

    def compile(self, changed_files: list[Path] | None = None) -> CompileResult:
        """
        Compile the Spring project.

        If ``changed_files`` is provided and the project is multi-module,
        compile only the affected module. Falls back to full compile when
        scope is unclear.
        """
        module = None
        if changed_files:
            modules = set()
            for f in changed_files:
                m = self._resolve_module(f)
                if m is not None:
                    modules.add(m)
            if len(modules) == 1:
                module = modules.pop()
            elif len(modules) > 1:
                module = None  # multiple modules → full compile

        cmd = ["mvn", "-q", "compile"]
        if module:
            cmd.extend(["-pl", f":{module}", "--am"])

        if self.dry_run:
            LOG.info("[dry-run] %s in %s", " ".join(cmd), self.spring_repo)
            return CompileResult(returncode=0, log="[dry-run]", errors=[], duration_sec=0.01)

        LOG.info("Running: %s in %s", " ".join(cmd), self.spring_repo)
        start = time.monotonic()
        proc = subprocess.run(
            cmd,
            cwd=str(self.spring_repo),
            capture_output=True,
            text=True,
        )
        duration = time.monotonic() - start

        log_text = (proc.stdout or "") + (proc.stderr or "")
        errors = self._parse_errors(log_text)

        result = CompileResult(
            returncode=proc.returncode,
            log=log_text,
            errors=errors,
            duration_sec=max(duration, 0.001),
        )

        LOG.info(
            "Compile %s in %.1fs (%d errors)",
            "ok" if result.returncode == 0 else "FAILED",
            result.duration_sec,
            len(result.errors),
        )
        return result

    def _resolve_module(self, file: Path) -> str | None:
        """
        Return Maven module artifactId for a given source file, or None for
        single-module projects.
        """
        resolved = file.resolve()
        # Walk up from the file to find the nearest pom.xml
        current = resolved.parent
        while current != current.parent:
            pom = current / "pom.xml"
            if pom.is_file() and current != self.spring_repo:
                # This is a sub-module
                cache_key = str(current)
                if cache_key not in self._module_cache:
                    self._module_cache[cache_key] = self._extract_artifact_id(pom)
                return self._module_cache[cache_key]
            if current == self.spring_repo:
                break
            current = current.parent

        return None  # single-module project

    def _extract_artifact_id(self, pom_path: Path) -> str | None:
        """Extract <artifactId> from a pom.xml (simple regex, no XML parser)."""
        try:
            content = pom_path.read_text(encoding="utf-8")
            # Match first <artifactId> that's a direct child (not in <parent>)
            # Simple heuristic: find artifactId after </parent> or at top level
            parent_end = content.find("</parent>")
            search_text = content[parent_end:] if parent_end >= 0 else content
            m = re.search(r"<artifactId>\s*([^<]+)\s*</artifactId>", search_text)
            if m:
                return m.group(1).strip()
        except OSError:
            pass
        return None

    def _parse_errors(self, log: str) -> list[dict]:
        """Parse Maven error log into structured error dicts."""
        errors: list[dict] = []
        for line in log.splitlines():
            m = MVN_ERROR_RE.search(line)
            if m:
                errors.append(
                    {
                        "file": m.group(1),
                        "line": int(m.group(2)),
                        "message": m.group(3).strip(),
                    }
                )
        return errors
