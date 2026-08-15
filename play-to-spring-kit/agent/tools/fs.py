"""Path-jailed filesystem tools handed to LLM agents.

Write access is restricted to the Spring repo's ``src/**``, ``pom.xml`` and
``.migration/**`` (deterministic-artifact area: decisions.md, journals, gap
reports -- not application source). The Play repo (when configured) is
readable only. Every write is recorded so the graph can feed changed files
to the incremental compiler.
"""

from __future__ import annotations

import re
from pathlib import Path

from langchain_core.tools import BaseTool, tool

MAX_READ_CHARS = 40_000
# Head+tail split for truncated reads (M6 Task 3): a Java file's imports sit
# at the top and the method actually under discussion is often near the
# bottom, so a single hard cut at MAX_READ_CHARS silently drops whichever
# half didn't fit -- usually the half that mattered. head gets the larger
# share since package/imports/class signature orient the model fastest.
_HEAD_CHARS = int(MAX_READ_CHARS * 0.6)
_TAIL_CHARS = MAX_READ_CHARS - _HEAD_CHARS

# tools/search below: directories never worth searching into.
_SEARCH_SKIP_DIRS = {"target", ".git", ".migration", "node_modules"}
_SEARCH_MAX_RESULTS_DEFAULT = 50
_SEARCH_MAX_OUTPUT_CHARS = 20_000

# run_tool_loop (agent/llm.py) recognizes this prefix on any tool's output
# and stops the loop early, capturing the rest as ToolLoopResult.manual_review_reason
# -- a generic convention, not coupled to FsJail specifically.
MANUAL_REVIEW_PREFIX = "MANUAL_REVIEW_REQUESTED: "

# Per-phase tool grants (M6 Task 9), lifted from the play-to-springboot
# plugin's role-based grants (dev gets Edit/Write, the others don't).
# - bootstrap/architect: creating fresh files from scratch (pom.xml,
#   Application.java, decisions.md) -- write_file is the natural tool, and
#   there's nothing existing to str_replace into yet.
# - compile_fix/runtime_wiring: editing EXISTING Java to fix a specific
#   failure -- str_replace over write_file (the plan's own steer -- whole-file
#   rewrite is both the expensive and the lossy path), plus the
#   diagnosis/learning tools since these are exactly the phases that hit a
#   library/pattern with no clean mapping.
# - routes: one annotation line above an existing method -- str_replace,
#   never a redesign, so no flag_for_manual_review/record_gap grant.
# - config_mapping: appends key=value lines; write_file kept (not just
#   str_replace) since the target file might not exist yet in an unusual
#   layout, matching pre-Task-9 behavior for this one phase.
_PHASE_TOOL_NAMES: dict[str, tuple[str, ...]] = {
    "bootstrap": ("read_file", "write_file", "list_dir", "search"),
    "architect": ("read_file", "write_file", "list_dir", "search"),
    "compile_fix": ("read_file", "str_replace", "list_dir", "search", "record_gap", "flag_for_manual_review"),
    "runtime_wiring": ("read_file", "str_replace", "list_dir", "search", "record_gap", "flag_for_manual_review"),
    "routes": ("read_file", "str_replace", "list_dir", "search"),
    "config_mapping": ("read_file", "write_file", "str_replace", "list_dir", "search"),
}


class FsJail:
    def __init__(self, spring_repo: Path, play_repo: Path | None = None, dry_run: bool = False) -> None:
        self.spring_repo = spring_repo.resolve()
        self.play_repo = play_repo.resolve() if play_repo else None
        self.edited_files: list[Path] = []
        # M6 Task 2: config.dry_run gates every other subprocess writer in
        # this codebase (toolkit_jar, setup_ops) but was never threaded into
        # FsJail -- write_file/str_replace wrote for real regardless. Writes
        # below still record the intended edit (edited_files, the return
        # message) so a dry-run trace looks the same as a real one to the
        # calling agent loop; only the actual filesystem mutation is skipped.
        self.dry_run = dry_run

    # ------------------------------------------------------------------
    def _resolve_read(self, raw: str) -> Path:
        p = Path(raw)
        if not p.is_absolute():
            p = self.spring_repo / p
        p = p.resolve()
        roots = [self.spring_repo] + ([self.play_repo] if self.play_repo else [])
        for root in roots:
            if p == root or p.is_relative_to(root):
                return p
        raise PermissionError(f"read outside allowed roots: {raw}")

    def _resolve_write(self, raw: str) -> Path:
        p = Path(raw)
        if not p.is_absolute():
            p = self.spring_repo / p
        p = p.resolve()
        allowed = (
            p == self.spring_repo / "pom.xml"
            or p.is_relative_to(self.spring_repo / "src")
            # M6 Task 6: .migration/ is the deterministic-artifact area
            # (journals, gap reports, decisions.md) -- not application
            # source, so widening write access here doesn't touch the
            # src/**+pom.xml invariant that protects the actual migrated
            # code. Lets the architect agent write decisions.md directly
            # via write_file instead of needing a second, deterministic
            # move-into-place step.
            or p.is_relative_to(self.spring_repo / ".migration")
        )
        if not allowed:
            raise PermissionError(
                f"write outside jail (allowed: src/**, pom.xml, .migration/** under {self.spring_repo}): {raw}"
            )
        return p

    def _record_edit(self, p: Path) -> None:
        if p not in self.edited_files:
            self.edited_files.append(p)

    # ------------------------------------------------------------------
    def build_tools(self, phase: str = "") -> list[BaseTool]:
        """`phase` selects a capability-scoped subset (M6 Task 9) -- capability
        removal over prompt instruction: a phase that never needs write_file
        doesn't get the tool, rather than being told in the prompt not to use
        it. An unrecognized/empty phase gets every tool, matching the
        pre-Task-9 default so no existing caller changes behavior unless it
        passes a known phase name."""
        jail = self

        @tool
        def read_file(path: str) -> str:
            """Read a text file. Path may be relative to the Spring repo root."""
            p = jail._resolve_read(path)
            text = p.read_text(encoding="utf-8", errors="replace")
            if len(text) <= MAX_READ_CHARS:
                return text
            head = text[:_HEAD_CHARS]
            tail = text[-_TAIL_CHARS:]
            omitted = len(text) - _HEAD_CHARS - _TAIL_CHARS
            return f"{head}\n... [{omitted} chars omitted -- use search to find a specific symbol] ...\n{tail}"

        @tool
        def write_file(path: str, content: str) -> str:
            """Overwrite (or create) a file inside the Spring repo src/ tree or pom.xml."""
            p = jail._resolve_write(path)
            if jail.dry_run:
                jail._record_edit(p)
                return f"[dry-run] would write {p}"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            jail._record_edit(p)
            return f"wrote {p}"

        @tool
        def str_replace(path: str, old: str, new: str) -> str:
            """Replace an exact unique occurrence of `old` with `new` in a file."""
            p = jail._resolve_write(path)
            text = p.read_text(encoding="utf-8", errors="replace")
            count = text.count(old)
            if count == 0:
                return "error: old string not found"
            if count > 1:
                # Line numbers of each occurrence (M6 Task 3), so the model
                # can widen `old` with real context instead of guessing
                # blindly at what makes the next attempt unique.
                lines_before_each = [text[:m.start()].count("\n") + 1 for m in re.finditer(re.escape(old), text)]
                return (
                    f"error: old string occurs {count} times at lines "
                    f"{', '.join(str(n) for n in lines_before_each)}; must be unique -- "
                    "include more surrounding context to disambiguate"
                )
            if jail.dry_run:
                jail._record_edit(p)
                return f"[dry-run] would edit {p}"
            p.write_text(text.replace(old, new, 1), encoding="utf-8")
            jail._record_edit(p)
            return f"edited {p}"

        @tool
        def list_dir(path: str = ".") -> str:
            """List a directory (relative to the Spring repo root by default)."""
            p = jail._resolve_read(path)
            if not p.is_dir():
                return f"error: not a directory: {path}"
            entries = sorted(p.iterdir(), key=lambda e: (e.is_file(), e.name))
            return "\n".join(f"{e.name}/" if e.is_dir() else e.name for e in entries[:200])

        @tool
        def search(pattern: str, path: str = ".", max_results: int = _SEARCH_MAX_RESULTS_DEFAULT) -> str:
            """Regex-search files under a directory (relative to the Spring repo
            root by default; also reaches the Play repo, read-only, if one is
            configured). Returns "path:line: text" per match, capped at
            max_results and at a total output-size budget -- narrow the pattern
            or the path if you see the truncation marker rather than assuming
            you saw every match."""
            root = jail._resolve_read(path)
            if not root.is_dir():
                return f"error: not a directory: {path}"
            try:
                regex = re.compile(pattern)
            except re.error as exc:
                return f"error: invalid regex: {exc}"

            results: list[str] = []
            output_chars = 0
            truncated = False
            for candidate in sorted(root.rglob("*")):
                if len(results) >= max_results:
                    truncated = True
                    break
                if not candidate.is_file():
                    continue
                if any(part in _SEARCH_SKIP_DIRS for part in candidate.parts):
                    continue
                try:
                    text = candidate.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                rel = candidate.relative_to(jail.spring_repo if candidate.is_relative_to(jail.spring_repo) else root)
                for lineno, line in enumerate(text.splitlines(), start=1):
                    if regex.search(line):
                        entry = f"{rel}:{lineno}: {line.strip()}"
                        output_chars += len(entry)
                        if output_chars > _SEARCH_MAX_OUTPUT_CHARS:
                            truncated = True
                            break
                        results.append(entry)
                        if len(results) >= max_results:
                            truncated = True
                            break
                if truncated:
                    break

            if not results:
                return "no matches"
            body = "\n".join(results)
            if truncated:
                body += f"\n... [truncated at {len(results)} matches -- narrow the pattern or path]"
            return body

        @tool
        def record_gap(kind: str, subject: str, what_i_did: str, layer: str = "") -> str:
            """Record that this tool had no rule for something you just had to
            improvise on -- NOT a bug in the migration (use flag_for_manual_review
            or just keep fixing for that), but a blind spot in THIS TOOL that the
            next Play repo will hit too. Call this in ADDITION to whatever fix you
            apply -- it never stops the loop.

            kind must be one of: unmapped_dependency (a build dependency with no
            decided Spring counterpart), unhandled_idiom (a Play API this tool's
            transformer and mapping tables both miss), tier_blind_spot (you know
            no verification tier can check what you produced), tool_error (a
            helper crashed or could not run), agent_improvised (no rule existed
            and you chose anyway), layout_surprise (a repo shape not expected),
            boot_failure (the app would not start and you don't know why).

            what_i_did is the load-bearing field: describe SPECIFICALLY what you
            chose in the absence of a rule (e.g. "hand-ported Akka actor to
            @Async"), not a vague "could not find a mapping" -- the specific
            choice is what makes this data actionable later. Record this even
            when your fix works -- especially then, since a gap that produced a
            passing result is the one nobody finds by looking at failures."""
            from .gaps import record_gap as _record_gap

            error = _record_gap(jail.spring_repo, kind, subject, what_i_did, layer=layer or None)
            if error is not None:
                return error
            return f"recorded gap: {kind} / {subject}"

        @tool
        def flag_for_manual_review(reason: str) -> str:
            """Call this INSTEAD of attempting further edits when you determine
            the failing code depends on a library/pattern with no Spring or
            Jakarta EE equivalent (e.g. an actor/reactive-streams framework
            like Akka/Pekko, a server-side templating engine, a DI framework
            with no drop-in replacement) -- so it needs a real redesign, not
            an incremental import/symbol fix. Explain specifically what's
            incompatible and, if you can tell, what a Spring-idiomatic
            replacement would look like. Stop editing after calling this."""
            # M6 Task 8: a stop is never a silent loss of the diagnosis -- the
            # agent's own explanation of what's incompatible is exactly the
            # what_i_did the gaps loop wants, so record it as a gap too, not
            # only as the (transient, per-run) manual_review_reason.
            from .gaps import record_gap as _record_gap

            _record_gap(jail.spring_repo, "agent_improvised", "flag_for_manual_review", reason, role="dev")
            return f"{MANUAL_REVIEW_PREFIX}{reason}"

        all_tools = {
            "read_file": read_file,
            "write_file": write_file,
            "str_replace": str_replace,
            "list_dir": list_dir,
            "search": search,
            "record_gap": record_gap,
            "flag_for_manual_review": flag_for_manual_review,
        }
        names = _PHASE_TOOL_NAMES.get(phase, tuple(all_tools))
        return [all_tools[name] for name in names]
