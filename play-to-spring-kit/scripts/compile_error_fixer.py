#!/usr/bin/env python3
"""
Taxonomy-driven deterministic compile-error fixer.

Runs after ``mvn compile`` and before any LLM call. Applies regex/text fixes
to Spring ``.java`` files for the ~12 known error types (E01–E12).
"""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

LOG = logging.getLogger("compile_error_fixer")

# ---------------------------------------------------------------------------
#  Data model
# ---------------------------------------------------------------------------


@dataclass
class FixResult:
    """Result of a deterministic fix pass."""

    fixed_count: int = 0
    unresolved: list[dict] = field(default_factory=list)
    det_fix_log: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
#  Taxonomy rules  (E01 – E12)
# ---------------------------------------------------------------------------

_FIXER_RULES: list[tuple[re.Pattern, str, str]] = [
    # E01: missing ObjectMapper import
    (
        re.compile(r"cannot find symbol.*class ObjectMapper", re.IGNORECASE),
        "E01",
        "import com.fasterxml.jackson.databind.ObjectMapper;",
    ),
    # E02: missing JsonNode import
    (
        re.compile(r"cannot find symbol.*class JsonNode", re.IGNORECASE),
        "E02",
        "import com.fasterxml.jackson.databind.JsonNode;",
    ),
    # E03: Play import not removed
    (
        re.compile(r"package play\.\w+ does not exist", re.IGNORECASE),
        "E03",
        "",  # handled specially: remove matching import line
    ),
    # E04: Results.ok() remnant
    (
        re.compile(r"cannot find symbol.*method ok\(", re.IGNORECASE),
        "E04",
        "",  # complex rewrite, pass to unresolved if not trivial
    ),
    # E05: incompatible types Result / ResponseEntity
    (
        re.compile(r"incompatible types.*Result.*ResponseEntity", re.IGNORECASE),
        "E05",
        "",  # complex rewrite, pass to unresolved if not trivial
    ),
    # E10: duplicate method
    (
        re.compile(r"method .* is already defined", re.IGNORECASE),
        "E10",
        "",  # handled specially: remove duplicate
    ),
    # E11: missing RestTemplate
    (
        re.compile(r"cannot find symbol.*class RestTemplate", re.IGNORECASE),
        "E11",
        "import org.springframework.web.client.RestTemplate;",
    ),
]

# Patterns indicating a deterministic fix produced invalid Java (bad-fix).
_BAD_FIX_PATTERNS = [
    re.compile(r"illegal start of expression", re.IGNORECASE),
    re.compile(r"reached end of file", re.IGNORECASE),
]

# Import line pattern
_IMPORT_LINE_RE = re.compile(r"^import\s+[\w.]+\s*;", re.MULTILINE)

# Play import line
_PLAY_IMPORT_RE = re.compile(r"^import\s+play\.\w[\w.]*\s*;\s*$", re.MULTILINE)


class CompileErrorFixer:
    """
    Taxonomy-driven rule engine that applies deterministic text/regex fixes
    to Spring ``.java`` files for known compile-error types.
    """

    def __init__(self, spring_repo: Path, dry_run: bool = False) -> None:
        self.spring_repo = spring_repo.resolve()
        self.dry_run = dry_run
        self._backed_up: dict[Path, Path] = {}  # original → .bak mapping

    # -------------------------------------------------------------------
    #  Public API
    # -------------------------------------------------------------------

    def run(self, errors: list[dict]) -> FixResult:
        """
        Apply all deterministic rules to the given error list.

        Returns a ``FixResult`` where ``fixed_count + len(unresolved) == len(errors)``.
        """
        result = FixResult()
        self._backed_up.clear()

        for error in errors:
            fixed = False
            file_path = self._resolve_file(error)
            if file_path is None:
                result.unresolved.append(error)
                continue

            msg = error.get("message", "")

            # Try each taxonomy rule in order
            for pattern, rule_id, import_line in _FIXER_RULES:
                if not pattern.search(msg):
                    continue

                if rule_id == "E03":
                    fixed = self._fix_play_import(file_path, error, result)
                elif rule_id == "E10":
                    fixed = self._fix_duplicate_method(file_path, error, result)
                elif rule_id in ("E01", "E02", "E11"):
                    fixed = self._fix_missing_import(file_path, import_line, rule_id, result)
                elif rule_id in ("E04", "E05"):
                    # Complex rewrites — pass to unresolved
                    fixed = False
                else:
                    fixed = False

                if fixed:
                    break

            if fixed:
                result.fixed_count += 1
            else:
                result.unresolved.append(error)

        return result

    def cleanup_backups(self) -> None:
        """Delete all ``.bak`` files created during the last run (call after successful compile)."""
        for _orig, bak in self._backed_up.items():
            if bak.exists():
                bak.unlink()
        self._backed_up.clear()

    def revert_bad_fixes(self, new_errors: list[dict]) -> list[dict]:
        """
        Check if any new errors indicate a bad fix (parse errors) on files we edited.
        Revert those files from ``.bak`` and return the errors that should be treated
        as unresolved instead.
        """
        reverted_errors: list[dict] = []
        for error in new_errors:
            msg = error.get("message", "")
            file_path = self._resolve_file(error)
            if file_path is None:
                continue
            if any(p.search(msg) for p in _BAD_FIX_PATTERNS):
                if file_path in self._backed_up:
                    bak = self._backed_up[file_path]
                    if bak.exists():
                        shutil.copy2(bak, file_path)
                        LOG.warning("Reverted bad fix on %s from backup", file_path.name)
                        reverted_errors.append(error)
        return reverted_errors

    # -------------------------------------------------------------------
    #  Private fix methods
    # -------------------------------------------------------------------

    def _resolve_file(self, error: dict) -> Path | None:
        """Resolve error file path; return None if outside spring_repo."""
        fp = error.get("file", "")
        if not fp:
            return None
        p = Path(fp)
        if not p.is_absolute():
            p = (self.spring_repo / p).resolve()
        else:
            p = p.resolve()

        # Safety: never modify files outside spring_repo
        try:
            p.relative_to(self.spring_repo)
        except ValueError:
            LOG.debug("Skipping file outside spring repo: %s", p)
            return None

        if not p.exists() or not p.is_file():
            return None
        return p

    def _backup(self, file_path: Path) -> None:
        """Create ``.bak`` backup before editing."""
        if file_path in self._backed_up:
            return  # already backed up
        bak = file_path.with_suffix(file_path.suffix + ".bak")
        shutil.copy2(file_path, bak)
        self._backed_up[file_path] = bak

    def _fix_missing_import(
        self, file_path: Path, import_line: str, rule_id: str, result: FixResult
    ) -> bool:
        """Add a missing import statement to a Java file."""
        content = file_path.read_text(encoding="utf-8")
        if import_line in content:
            return True  # already present — count as fixed

        # Find the last import line and insert after it
        last_import_match = None
        for m in _IMPORT_LINE_RE.finditer(content):
            last_import_match = m

        if last_import_match is None:
            # No imports — insert before first class/interface/enum
            insert_pos = 0
            for kw in ["public class ", "class ", "public interface ", "interface ", "public enum ", "enum "]:
                idx = content.find(kw)
                if idx >= 0:
                    insert_pos = idx
                    break
            new_content = content[:insert_pos] + import_line + "\n\n" + content[insert_pos:]
        else:
            insert_pos = last_import_match.end()
            new_content = content[:insert_pos] + "\n" + import_line + content[insert_pos:]

        if self.dry_run:
            result.det_fix_log.append(f"[dry-run] {rule_id}: would add '{import_line}' to {file_path.name}")
            return True

        self._backup(file_path)
        file_path.write_text(new_content, encoding="utf-8")
        result.det_fix_log.append(f"{rule_id}: added '{import_line}' to {file_path.name}")
        return True

    def _fix_play_import(self, file_path: Path, error: dict, result: FixResult) -> bool:
        """E03: Remove play.* import lines matching the error message."""
        msg = error.get("message", "")
        # Extract the package name from "package play.xxx does not exist"
        pkg_match = re.search(r"package\s+(play\.\w[\w.]*)\s+does not exist", msg, re.IGNORECASE)
        if not pkg_match:
            return False
        play_pkg = pkg_match.group(1)

        content = file_path.read_text(encoding="utf-8")
        pattern = re.compile(
            r"^import\s+" + re.escape(play_pkg) + r"[\w.]*\s*;\s*$",
            re.MULTILINE,
        )
        new_content, count = pattern.subn("", content)
        if count == 0:
            return False

        # Clean up blank lines from removal
        new_content = re.sub(r"\n{3,}", "\n\n", new_content)

        if self.dry_run:
            result.det_fix_log.append(f"[dry-run] E03: would remove {count} play import(s) from {file_path.name}")
            return True

        self._backup(file_path)
        file_path.write_text(new_content, encoding="utf-8")
        result.det_fix_log.append(f"E03: removed {count} play import(s) matching '{play_pkg}' from {file_path.name}")
        return True

    def _fix_duplicate_method(self, file_path: Path, error: dict, result: FixResult) -> bool:
        """E10: Remove duplicate method, keeping the Spring-idiomatic version."""
        msg = error.get("message", "")
        line_num = error.get("line", 0)
        if not line_num:
            return False

        content = file_path.read_text(encoding="utf-8")
        lines = content.split("\n")

        if line_num < 1 or line_num > len(lines):
            return False

        # Find the method at the error line and try to remove it
        # This is a basic heuristic: find the method start (line_num - 1 in 0-indexed)
        # and trace through brace matching to find the end
        start_idx = line_num - 1  # 0-indexed

        # Look backwards for method signature / annotations
        method_start = start_idx
        while method_start > 0:
            prev = lines[method_start - 1].strip()
            if prev.startswith("@") or prev == "":
                method_start -= 1
            else:
                break

        # Find the end by brace matching from the start
        brace_count = 0
        method_end = start_idx
        found_open = False
        for i in range(start_idx, len(lines)):
            brace_count += lines[i].count("{") - lines[i].count("}")
            if "{" in lines[i]:
                found_open = True
            if found_open and brace_count == 0:
                method_end = i
                break

        if not found_open:
            return False

        # Remove the duplicate method
        removed_lines = lines[method_start : method_end + 1]
        new_lines = lines[:method_start] + lines[method_end + 1 :]
        new_content = "\n".join(new_lines)

        if self.dry_run:
            result.det_fix_log.append(
                f"[dry-run] E10: would remove duplicate method at line {line_num} in {file_path.name}"
            )
            return True

        self._backup(file_path)
        file_path.write_text(new_content, encoding="utf-8")
        result.det_fix_log.append(
            f"E10: removed duplicate method at line {line_num} ({len(removed_lines)} lines) in {file_path.name}"
        )
        return True
