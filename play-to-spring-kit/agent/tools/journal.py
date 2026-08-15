"""Per-file/per-batch crash recovery journal (M6 Task 7).

The sqlite checkpoint (checkpoint.py) only advances at graph *node*
boundaries. `transform_node` calls `migrate_until_done`, which itself loops
over several `migrate-app --batch-size N` subprocess calls before returning
-- a crash mid-loop loses every batch that loop already completed, because
the checkpoint was never touched. This module is the finer-grained record
that survives that crash: one NDJSON line appended after each batch, folded
back into `migration_units[i].files_migrated` the next time the graph reads
state for that unit.

Lifted from the play-to-springboot plugin's journal discipline
(docs/STATE-CONTRACT.md "Why the journal is append-only"), including its two
hard-won details:

- **Append-only, never rewritten.** A truncated final line is simply skipped
  by the reader; every completed line before it is still good. This bounded
  damage is the whole reason for NDJSON over one JSON array -- truncate an
  array mid-write and the entire file is unreadable, not just the last entry.
- **Idempotent folding via journal_offsets.** The plugin's bug: a
  re-dispatched three-file layer reported nine files migrated, because a
  naive "read the whole file and sum" re-fold recounts lines already folded
  on a prior pass. `fold_journal` takes the offset already consumed and
  returns both the new lines' effect AND the new offset -- callers persist
  the returned offset (in `state["journal_offsets"]`) and never re-derive it.

Torn-line handling: `write_entry` always starts its append with a leading
newline, so a process killed mid-write leaves the torn fragment isolated on
its own line rather than welded onto the previous complete entry. Because
that discipline only protects future appends, not a line that's already
torn, `_parse_line` accepts a best-effort salvage: if the line as a whole
doesn't parse, try the shortest trailing suffix (starting at each `{`) that
both parses as JSON *and* has an "action" key -- so a nested object inside
one well-formed entry is never mistaken for a second, separate entry.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def journal_path(spring_repo: Path, unit_id: str) -> Path:
    safe_id = unit_id.replace("/", "_") or "default"
    return spring_repo / ".migration" / "journal" / f"{safe_id}.ndjson"


def write_entry(spring_repo: Path, unit_id: str, entry: dict[str, Any]) -> None:
    """Append one journal line. Leading newline on every append (not just
    after the first) is deliberate: it isolates a torn final line from
    whatever gets appended next, at the cost of one blank line per entry
    that _iter_lines already strips."""
    path = journal_path(spring_repo, unit_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write("\n" + json.dumps(entry, default=str))


def _salvage_trailing_entry(line: str) -> dict[str, Any] | None:
    """A welded line (torn write immediately followed by a new append) has
    the previous entry's tail concatenated with the next entry's start, with
    no separator. Try every '{' position as a possible start of a second,
    well-formed entry; accept the FIRST (leftmost, so the shortest lost
    fragment) suffix that both parses and carries an "action" key -- a
    nested object inside one legitimate entry has no "action" key of its own
    at that nesting level, so it's never mistaken for a second entry."""
    for i, ch in enumerate(line):
        if ch != "{":
            continue
        candidate = line[i:]
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and "action" in parsed:
            return parsed
    return None


def _parse_line(line: str) -> dict[str, Any] | None:
    line = line.strip()
    if not line:
        return None
    try:
        parsed = json.loads(line)
        if isinstance(parsed, dict):
            return parsed
        return None
    except json.JSONDecodeError:
        return _salvage_trailing_entry(line)


def _iter_valid_lines(path: Path) -> list[dict[str, Any]]:
    """Every parseable line, in file order. A truncated final line (the
    process died mid-write) fails to parse and is silently skipped -- every
    completed line before it is still returned."""
    if not path.is_file():
        return []
    raw_lines = path.read_text(encoding="utf-8", errors="replace").split("\n")
    entries: list[dict[str, Any]] = []
    for raw in raw_lines:
        parsed = _parse_line(raw)
        if parsed is not None:
            entries.append(parsed)
    return entries


def fold_journal(
    spring_repo: Path,
    unit_id: str,
    journal_offsets: dict[str, int],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Returns (new_entries_since_last_fold, updated journal_offsets).

    Idempotent: calling this twice in a row with the SAME journal_offsets
    dict (i.e. before persisting the returned one) returns the same
    new_entries both times -- it never mutates the input dict, callers must
    persist the returned one for the next fold to start where this one left
    off.

    Offset is counted in valid (parseable) lines, not raw file lines --
    matches what write_entry actually appends per call, so a truncated final
    line short of a full entry naturally leaves the offset one entry behind,
    and the next write_entry call's completed line is what pushes the count
    forward, not the partial fragment.
    """
    key = journal_path(spring_repo, unit_id).name
    entries = _iter_valid_lines(journal_path(spring_repo, unit_id))
    offset = journal_offsets.get(key, 0)

    # A journal shorter than its recorded offset cannot be the file that
    # offset was measured against (fresh/reset journal under the same unit
    # id) -- replay it from the top rather than silently returning nothing.
    if offset > len(entries):
        offset = 0

    new_entries = entries[offset:]
    updated_offsets = dict(journal_offsets)
    updated_offsets[key] = len(entries)
    return new_entries, updated_offsets


def fold_migrated_count(new_entries: list[dict[str, Any]]) -> int:
    """Sum of `count` across every 'migrated' action entry -- the value
    nodes/slice_pipeline.py folds into migration_units[i].files_migrated."""
    return sum(int(e.get("count", 0)) for e in new_entries if e.get("action") == "migrated")
