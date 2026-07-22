"""Path-jailed filesystem tools handed to LLM agents.

Write access is restricted to the Spring repo's ``src/**``, ``pom.xml`` and
``src/main/resources/**``. The Play repo (when configured) is readable only.
Every write is recorded so the graph can feed changed files to the
incremental compiler.
"""

from __future__ import annotations

from pathlib import Path

from langchain_core.tools import BaseTool, tool

MAX_READ_CHARS = 40_000


class FsJail:
    def __init__(self, spring_repo: Path, play_repo: Path | None = None) -> None:
        self.spring_repo = spring_repo.resolve()
        self.play_repo = play_repo.resolve() if play_repo else None
        self.edited_files: list[Path] = []

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
        )
        if not allowed:
            raise PermissionError(
                f"write outside jail (allowed: src/**, pom.xml under {self.spring_repo}): {raw}"
            )
        return p

    def _record_edit(self, p: Path) -> None:
        if p not in self.edited_files:
            self.edited_files.append(p)

    # ------------------------------------------------------------------
    def build_tools(self) -> list[BaseTool]:
        jail = self

        @tool
        def read_file(path: str) -> str:
            """Read a text file. Path may be relative to the Spring repo root."""
            p = jail._resolve_read(path)
            text = p.read_text(encoding="utf-8", errors="replace")
            if len(text) > MAX_READ_CHARS:
                return text[:MAX_READ_CHARS] + f"\n... [truncated at {MAX_READ_CHARS} chars]"
            return text

        @tool
        def write_file(path: str, content: str) -> str:
            """Overwrite (or create) a file inside the Spring repo src/ tree or pom.xml."""
            p = jail._resolve_write(path)
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
                return f"error: old string occurs {count} times; must be unique"
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

        return [read_file, write_file, str_replace, list_dir]
