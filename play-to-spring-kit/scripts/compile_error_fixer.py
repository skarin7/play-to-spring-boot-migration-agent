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
#  Patterns
# ---------------------------------------------------------------------------

# Patterns indicating a deterministic fix produced invalid Java (bad-fix).
_BAD_FIX_PATTERNS = [
    re.compile(r"illegal start of expression", re.IGNORECASE),
    re.compile(r"reached end of file", re.IGNORECASE),
]

# Import line pattern (matches a single import statement)
_IMPORT_LINE_RE = re.compile(r"^import\s+[\w.]+\s*;", re.MULTILINE)

# Package declaration pattern
_PACKAGE_RE = re.compile(r"^package\s+([\w.]+)\s*;", re.MULTILINE)

# Spring annotations that indicate a method is Spring-idiomatic
_SPRING_ANNOTATIONS = re.compile(
    r"@(GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping|RequestMapping|ResponseBody)",
    re.IGNORECASE,
)
_SPRING_TYPES = re.compile(r"ResponseEntity", re.IGNORECASE)

# RestTemplateConfig template
_REST_TEMPLATE_CONFIG = """\
package {base_package}.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.client.RestTemplate;

@Configuration
public class RestTemplateConfig {{
    @Bean
    public RestTemplate restTemplate() {{ return new RestTemplate(); }}
}}
"""


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
            fixed = self._apply_rules(error, result)
            if fixed:
                result.fixed_count += 1
            else:
                result.unresolved.append(error)

        # Invariant check (defensive)
        assert result.fixed_count + len(result.unresolved) == len(errors), (
            f"Losslessness violated: {result.fixed_count} + {len(result.unresolved)} != {len(errors)}"
        )
        return result

    def delete_bak_files(self) -> None:
        """Delete all ``.bak`` files created during the last run (call after successful compile)."""
        for _orig, bak in list(self._backed_up.items()):
            if bak.exists():
                bak.unlink()
        self._backed_up.clear()

    # Keep old name as alias for compatibility
    cleanup_backups = delete_bak_files

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
    #  Rule dispatch
    # -------------------------------------------------------------------

    def _apply_rules(self, error: dict, result: FixResult) -> bool:
        """Try each taxonomy rule in order; return True if any rule applied."""
        msg = error.get("message", "")
        file_path = self._resolve_file(error)

        # E01
        if re.search(r"cannot find symbol.*class ObjectMapper", msg, re.IGNORECASE):
            if file_path is None:
                return False
            return self._fix_add_import(
                file_path, "import com.fasterxml.jackson.databind.ObjectMapper;",
                "E01", "added ObjectMapper import", error, result,
            )

        # E02
        if re.search(r"cannot find symbol.*class JsonNode", msg, re.IGNORECASE):
            if file_path is None:
                return False
            return self._fix_add_import(
                file_path, "import com.fasterxml.jackson.databind.JsonNode;",
                "E02", "added JsonNode import", error, result,
            )

        # E03
        if re.search(r"package play\.\w+ does not exist", msg, re.IGNORECASE):
            if file_path is None:
                return False
            return self._fix_play_import(file_path, error, result)

        # E04
        if re.search(r"cannot find symbol.*method ok\(", msg, re.IGNORECASE):
            if file_path is None:
                return False
            return self._fix_ok_call(file_path, error, result)

        # E05
        if re.search(r"incompatible types.*Result.*ResponseEntity", msg, re.IGNORECASE):
            if file_path is None:
                return False
            return self._fix_return_type(file_path, error, result)

        # E10
        if re.search(r"method .* is already defined", msg, re.IGNORECASE):
            if file_path is None:
                return False
            return self._fix_duplicate_method(file_path, error, result)

        # E11
        if re.search(r"cannot find symbol.*class RestTemplate", msg, re.IGNORECASE):
            if file_path is None:
                return False
            return self._fix_rest_template(file_path, error, result)

        return False

    # -------------------------------------------------------------------
    #  Import helper
    # -------------------------------------------------------------------

    def _add_import(self, file_path: Path, import_line: str) -> bool:
        """
        Insert *import_line* into *file_path* after the last existing import.

        Returns False if the import already exists (no-op), True if inserted.
        Does NOT write if dry_run — caller must check.
        """
        content = file_path.read_text(encoding="utf-8")
        if import_line in content:
            return False  # already present

        # Find the last import line
        last_import_match = None
        for m in _IMPORT_LINE_RE.finditer(content):
            last_import_match = m

        if last_import_match is not None:
            insert_pos = last_import_match.end()
            new_content = content[:insert_pos] + "\n" + import_line + content[insert_pos:]
        else:
            # No imports — insert after package declaration
            pkg_match = _PACKAGE_RE.search(content)
            if pkg_match:
                insert_pos = pkg_match.end()
                new_content = content[:insert_pos] + "\n\n" + import_line + "\n" + content[insert_pos:]
            else:
                # No package either — prepend
                new_content = import_line + "\n" + content

        self._backup(file_path)
        file_path.write_text(new_content, encoding="utf-8")
        return True

    # -------------------------------------------------------------------
    #  Private fix methods
    # -------------------------------------------------------------------

    def _fix_add_import(
        self,
        file_path: Path,
        import_line: str,
        rule_id: str,
        description: str,
        error: dict,
        result: FixResult,
    ) -> bool:
        """Generic: add a single import to a file."""
        content = file_path.read_text(encoding="utf-8")
        if import_line in content:
            # Already present — still counts as fixed
            result.det_fix_log.append(
                f"{rule_id}: {description} (already present) in {file_path.name}:{error.get('line', '?')}"
            )
            return True

        if self.dry_run:
            result.det_fix_log.append(
                f"[dry-run] {rule_id}: would {description} to {file_path.name}:{error.get('line', '?')}"
            )
            return False  # dry_run: no actual fix

        added = self._add_import(file_path, import_line)
        if added:
            result.det_fix_log.append(
                f"{rule_id}: {description} to {file_path.name}:{error.get('line', '?')}"
            )
        return added

    def _fix_play_import(self, file_path: Path, error: dict, result: FixResult) -> bool:
        """E03: Remove play.* import lines matching the error message."""
        msg = error.get("message", "")
        pkg_match = re.search(r"package\s+(play\.\w[\w.]*)\s+does not exist", msg, re.IGNORECASE)
        if not pkg_match:
            return False
        play_pkg = pkg_match.group(1)

        content = file_path.read_text(encoding="utf-8")
        pattern = re.compile(
            r"^import\s+" + re.escape(play_pkg) + r"[\w.]*\s*;\s*\n?",
            re.MULTILINE,
        )
        new_content, count = pattern.subn("", content)
        if count == 0:
            return False

        # Clean up excess blank lines
        new_content = re.sub(r"\n{3,}", "\n\n", new_content)

        if self.dry_run:
            result.det_fix_log.append(
                f"[dry-run] E03: would remove {count} play import(s) from {file_path.name}:{error.get('line', '?')}"
            )
            return False

        self._backup(file_path)
        file_path.write_text(new_content, encoding="utf-8")
        result.det_fix_log.append(
            f"E03: removed {count} play import(s) matching '{play_pkg}' from {file_path.name}:{error.get('line', '?')}"
        )
        return True

    def _fix_ok_call(self, file_path: Path, error: dict, result: FixResult) -> bool:
        """E04: Rewrite ok(...) → ResponseEntity.ok(...) at the error line."""
        line_num = error.get("line", 0)
        content = file_path.read_text(encoding="utf-8")
        lines = content.split("\n")

        if not line_num or line_num < 1 or line_num > len(lines):
            return False

        idx = line_num - 1  # 0-indexed
        original_line = lines[idx]

        # Replace `ok(` only when not already prefixed with ResponseEntity.
        new_line = re.sub(r"(?<!ResponseEntity\.)(?<!\w)ok\(", "ResponseEntity.ok(", original_line)
        if new_line == original_line:
            return False

        lines[idx] = new_line
        new_content = "\n".join(lines)

        if self.dry_run:
            result.det_fix_log.append(
                f"[dry-run] E04: would rewrite ok( → ResponseEntity.ok( in {file_path.name}:{line_num}"
            )
            return False

        self._backup(file_path)
        file_path.write_text(new_content, encoding="utf-8")
        result.det_fix_log.append(
            f"E04: rewrote ok( → ResponseEntity.ok( in {file_path.name}:{line_num}"
        )

        # Add ResponseEntity import if missing
        re_import = "import org.springframework.http.ResponseEntity;"
        if re_import not in new_content:
            self._add_import(file_path, re_import)
            result.det_fix_log.append(
                f"E04: added ResponseEntity import to {file_path.name}"
            )

        return True

    def _fix_return_type(self, file_path: Path, error: dict, result: FixResult) -> bool:
        """E05: Change the enclosing method's return type to ResponseEntity<JsonNode>."""
        line_num = error.get("line", 0)
        content = file_path.read_text(encoding="utf-8")
        lines = content.split("\n")

        if not line_num or line_num < 1 or line_num > len(lines):
            return False

        # Walk backwards from error line to find the method signature
        method_sig_idx = None
        # Pattern: visibility modifier + return type + method name + (
        method_sig_re = re.compile(
            r"(public|protected|private)\s+\S+\s+\w+\s*\("
        )
        for i in range(line_num - 1, max(line_num - 30, -1), -1):
            if method_sig_re.search(lines[i]):
                method_sig_idx = i
                break

        if method_sig_idx is None:
            return False

        original_sig_line = lines[method_sig_idx]
        # Replace the return type (second token after visibility modifier)
        new_sig_line = re.sub(
            r"((?:public|protected|private)\s+)\S+(\s+\w+\s*\()",
            r"\1ResponseEntity<JsonNode>\2",
            original_sig_line,
        )
        if new_sig_line == original_sig_line:
            return False

        lines[method_sig_idx] = new_sig_line
        new_content = "\n".join(lines)

        if self.dry_run:
            result.det_fix_log.append(
                f"[dry-run] E05: would change return type to ResponseEntity<JsonNode> in {file_path.name}:{line_num}"
            )
            return False

        self._backup(file_path)
        file_path.write_text(new_content, encoding="utf-8")
        result.det_fix_log.append(
            f"E05: changed return type to ResponseEntity<JsonNode> in {file_path.name}:{line_num}"
        )

        # Add required imports
        for imp in (
            "import org.springframework.http.ResponseEntity;",
            "import com.fasterxml.jackson.databind.JsonNode;",
        ):
            current = file_path.read_text(encoding="utf-8")
            if imp not in current:
                self._add_import(file_path, imp)
                result.det_fix_log.append(
                    f"E05: added {imp.split()[-1].rstrip(';')} import to {file_path.name}"
                )

        return True

    def _fix_duplicate_method(self, file_path: Path, error: dict, result: FixResult) -> bool:
        """E10: Remove duplicate method, keeping the Spring-idiomatic version."""
        line_num = error.get("line", 0)
        if not line_num:
            return False

        content = file_path.read_text(encoding="utf-8")
        lines = content.split("\n")

        if line_num < 1 or line_num > len(lines):
            return False

        # Extract the method name from the error message
        msg = error.get("message", "")
        name_match = re.search(r"method\s+(\w+)\s*\(", msg, re.IGNORECASE)
        method_name = name_match.group(1) if name_match else None

        # Find all method blocks with this name
        method_blocks = self._find_method_blocks(lines, method_name)

        if len(method_blocks) < 2:
            # Fall back: remove the block at the error line
            return self._remove_method_at_line(lines, line_num, file_path, error, result)

        # Decide which to remove: keep Spring-idiomatic, remove the other
        def is_spring_idiomatic(block_lines: list[str]) -> bool:
            text = "\n".join(block_lines)
            return bool(_SPRING_ANNOTATIONS.search(text) or _SPRING_TYPES.search(text))

        spring_flags = [is_spring_idiomatic(b["lines"]) for b in method_blocks]

        if spring_flags[0] and not spring_flags[1]:
            to_remove = method_blocks[1]
        elif spring_flags[1] and not spring_flags[0]:
            to_remove = method_blocks[0]
        else:
            # Both or neither — remove the second occurrence
            to_remove = method_blocks[1]

        start, end = to_remove["start"], to_remove["end"]
        new_lines = lines[:start] + lines[end + 1:]
        new_content = "\n".join(new_lines)

        if self.dry_run:
            result.det_fix_log.append(
                f"[dry-run] E10: would remove duplicate method '{method_name}' in {file_path.name}:{line_num}"
            )
            return False

        self._backup(file_path)
        file_path.write_text(new_content, encoding="utf-8")
        result.det_fix_log.append(
            f"E10: removed duplicate method '{method_name}' (lines {start+1}–{end+1}) in {file_path.name}:{line_num}"
        )
        return True

    def _fix_rest_template(self, file_path: Path, error: dict, result: FixResult) -> bool:
        """E11: Generate RestTemplateConfig.java and add RestTemplate import to affected file."""
        content = file_path.read_text(encoding="utf-8")

        # Derive base_package from the file's package declaration
        pkg_match = _PACKAGE_RE.search(content)
        if not pkg_match:
            return False
        base_package = pkg_match.group(1)
        # Strip sub-packages to get the root package (up to 3 levels)
        parts = base_package.split(".")
        # Use the full package as base (config will be appended)
        # but strip any trailing sub-package like .controller, .service, etc.
        known_suffixes = {"controller", "service", "repository", "model", "dto", "util", "config"}
        while parts and parts[-1].lower() in known_suffixes:
            parts.pop()
        base_package = ".".join(parts)

        # Determine config directory path
        # Convert base_package to path: com.example.app → com/example/app
        pkg_path = Path(*base_package.split("."))
        config_dir = self.spring_repo / "src" / "main" / "java" / pkg_path / "config"

        config_file = config_dir / "RestTemplateConfig.java"

        if self.dry_run:
            result.det_fix_log.append(
                f"[dry-run] E11: would generate {config_file} and add RestTemplate import to {file_path.name}"
            )
            return False

        # Write config file (safe — it's under spring_repo)
        try:
            config_dir.mkdir(parents=True, exist_ok=True)
            config_content = _REST_TEMPLATE_CONFIG.format(base_package=base_package)
            config_file.write_text(config_content, encoding="utf-8")
            result.det_fix_log.append(
                f"E11: generated {config_file.relative_to(self.spring_repo)}"
            )
        except OSError as exc:
            LOG.error("Failed to write RestTemplateConfig.java: %s", exc)
            return False

        # Add RestTemplate import to the affected file
        rt_import = "import org.springframework.web.client.RestTemplate;"
        if rt_import not in content:
            self._add_import(file_path, rt_import)
            result.det_fix_log.append(
                f"E11: added RestTemplate import to {file_path.name}:{error.get('line', '?')}"
            )

        return True

    # -------------------------------------------------------------------
    #  Utility helpers
    # -------------------------------------------------------------------

    def _resolve_file(self, error: dict) -> Path | None:
        """Resolve error file path; return None if outside spring_repo or missing."""
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
        """Create ``.bak`` backup before editing (only once per file per run)."""
        if file_path in self._backed_up:
            return
        bak = file_path.with_suffix(file_path.suffix + ".bak")
        shutil.copy2(file_path, bak)
        self._backed_up[file_path] = bak

    def _find_method_blocks(self, lines: list[str], method_name: str | None) -> list[dict]:
        """
        Find all method blocks (with annotations) for *method_name* in *lines*.
        Returns list of dicts with keys: start (inclusive), end (inclusive), lines.
        """
        blocks = []
        method_re = re.compile(
            r"(public|protected|private)\s+\S+\s+" + re.escape(method_name or "") + r"\s*\(",
        ) if method_name else re.compile(
            r"(public|protected|private)\s+\S+\s+\w+\s*\("
        )

        i = 0
        while i < len(lines):
            if method_re.search(lines[i]):
                # Walk back to include annotations
                block_start = i
                while block_start > 0 and lines[block_start - 1].strip().startswith("@"):
                    block_start -= 1

                # Walk forward to find end of method body via brace matching
                brace_count = 0
                found_open = False
                block_end = i
                for j in range(i, len(lines)):
                    brace_count += lines[j].count("{") - lines[j].count("}")
                    if "{" in lines[j]:
                        found_open = True
                    if found_open and brace_count == 0:
                        block_end = j
                        break

                if found_open:
                    blocks.append({
                        "start": block_start,
                        "end": block_end,
                        "lines": lines[block_start:block_end + 1],
                    })
                    i = block_end + 1
                    continue
            i += 1

        return blocks

    def _remove_method_at_line(
        self, lines: list[str], line_num: int, file_path: Path, error: dict, result: FixResult
    ) -> bool:
        """Fallback: remove the method block that contains *line_num*."""
        start_idx = line_num - 1

        # Walk back for annotations
        method_start = start_idx
        while method_start > 0 and lines[method_start - 1].strip().startswith("@"):
            method_start -= 1

        # Brace-match to find end
        brace_count = 0
        found_open = False
        method_end = start_idx
        for i in range(start_idx, len(lines)):
            brace_count += lines[i].count("{") - lines[i].count("}")
            if "{" in lines[i]:
                found_open = True
            if found_open and brace_count == 0:
                method_end = i
                break

        if not found_open:
            return False

        new_lines = lines[:method_start] + lines[method_end + 1:]
        new_content = "\n".join(new_lines)

        if self.dry_run:
            result.det_fix_log.append(
                f"[dry-run] E10: would remove method at line {line_num} in {file_path.name}"
            )
            return False

        self._backup(file_path)
        file_path.write_text(new_content, encoding="utf-8")
        result.det_fix_log.append(
            f"E10: removed duplicate method at line {line_num} in {file_path.name}"
        )
        return True
