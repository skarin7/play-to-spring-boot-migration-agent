#!/usr/bin/env python3
"""
Incremental Maven compiler wrapper.

Replaces full ``mvn compile`` (entire project) with targeted compilation of only
changed files/modules, reducing compile time on large multi-module projects.

Strategy:
  - Single-module projects: always ``mvn -q compile`` (no change)
  - Multi-module with changed_files: ``mvn -q compile -pl :<module> --am``
  - Falls back to full compile when module cannot be determined
"""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from xml.etree import ElementTree

# Maven error line: [ERROR] /path/File.java:[10,20] error: message
MVN_ERROR_RE = re.compile(r"\[ERROR\]\s+(.+\.java):\[(\d+),[^\]]+\]\s*(.+)")

# Infrastructure error patterns (JDK crash, Lombok annotation processor failure)
_INFRA_PATTERNS = [
    re.compile(r"fatal error compiling", re.I),
    re.compile(r"ExceptionInInitializerError", re.I),
    re.compile(r"TypeTag.*unknown", re.I),
    re.compile(r"annotation processing", re.I),
]


@dataclass
class CompileResult:
    returncode: int
    log: str
    errors: list[dict[str, Any]] = field(default_factory=list)
    duration_sec: float = 0.0

    @property
    def is_infrastructure_error(self) -> bool:
        """True if the log contains a JDK/Lombok crash rather than source errors."""
        return any(p.search(self.log) for p in _INFRA_PATTERNS) and not self.errors


class IncrementalCompiler:
    """
    Wraps ``mvn compile`` with optional module targeting.

    Postconditions:
    - returncode == 0  iff  errors is empty
    - duration_sec > 0
    """

    def __init__(self, spring_repo: Path, dry_run: bool = False) -> None:
        self.spring_repo = spring_repo.resolve()
        self.dry_run = dry_run
        self._module_cache: dict[str, Optional[str]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compile(self, changed_files: Optional[list[Path]] = None) -> CompileResult:
        """
        Compile the Spring project.

        If *changed_files* is provided and the project is multi-module, compiles
        only the module containing those files. Falls back to full compile when
        the module cannot be determined or the project is single-module.
        """
        module = None
        if changed_files:
            modules = {self._resolve_module(f) for f in changed_files}
            modules.discard(None)
            if len(modules) == 1:
                module = next(iter(modules))

        if module:
            cmd = ["mvn", "-q", "compile", f"-pl", f":{module}", "--am"]
        else:
            cmd = ["mvn", "-q", "compile"]

        return self._run(cmd)

    # ------------------------------------------------------------------
    # Module resolution
    # ------------------------------------------------------------------

    def _resolve_module(self, file: Path) -> Optional[str]:
        """
        Return the Maven module artifactId that contains *file*, or None for
        single-module projects or when the module cannot be determined.
        """
        file = file.resolve()
        cache_key = str(file)
        if cache_key in self._module_cache:
            return self._module_cache[cache_key]

        result = self._find_module_for_file(file)
        self._module_cache[cache_key] = result
        return result

    def _find_module_for_file(self, file: Path) -> Optional[str]:
        """Walk up from *file* looking for a pom.xml that declares a module."""
        # Collect all pom.xml files under spring_repo
        pom_files = list(self.spring_repo.rglob("pom.xml"))
        if len(pom_files) <= 1:
            # Single-module project
            return None

        # Find the pom.xml whose directory is the closest ancestor of file
        best_pom: Optional[Path] = None
        best_depth = -1
        for pom in pom_files:
            pom_dir = pom.parent.resolve()
            try:
                file.relative_to(pom_dir)
                depth = len(pom_dir.parts)
                if depth > best_depth:
                    best_depth = depth
                    best_pom = pom
            except ValueError:
                continue

        if best_pom is None or best_pom == self.spring_repo / "pom.xml":
            return None

        return self._artifact_id_from_pom(best_pom)

    @staticmethod
    def _artifact_id_from_pom(pom: Path) -> Optional[str]:
        """Extract <artifactId> from a pom.xml (first occurrence, direct child of <project>)."""
        try:
            tree = ElementTree.parse(str(pom))
            root = tree.getroot()
            # Handle namespace
            ns = ""
            if root.tag.startswith("{"):
                ns = root.tag.split("}")[0] + "}"
            elem = root.find(f"{ns}artifactId")
            if elem is not None and elem.text:
                return elem.text.strip()
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def _run(self, cmd: list[str]) -> CompileResult:
        if self.dry_run:
            return CompileResult(returncode=0, log="", errors=[], duration_sec=0.001)

        start = time.monotonic()
        proc = subprocess.run(
            cmd,
            cwd=str(self.spring_repo),
            capture_output=True,
            text=True,
            timeout=3600,
        )
        duration = time.monotonic() - start
        log = (proc.stdout or "") + (proc.stderr or "")
        errors = _parse_errors(log)

        result = CompileResult(
            returncode=proc.returncode,
            log=log,
            errors=errors,
            duration_sec=max(duration, 0.001),
        )

        # Enforce postcondition: returncode==0 iff errors is empty
        if result.returncode == 0 and result.errors:
            # Parsed errors from a successful compile — clear them (shouldn't happen)
            result.errors = []
        elif result.returncode != 0 and not result.errors and not result.is_infrastructure_error:
            # Non-zero exit but no parsed errors — add a sentinel
            result.errors = [{"file": "unknown", "line": 0, "message": log[-2000:].strip()}]

        return result


def _parse_errors(log: str) -> list[dict[str, Any]]:
    errors = []
    for line in log.splitlines():
        m = MVN_ERROR_RE.search(line)
        if m:
            errors.append({
                "file": m.group(1),
                "line": int(m.group(2)),
                "message": m.group(3).strip(),
            })
    return errors
