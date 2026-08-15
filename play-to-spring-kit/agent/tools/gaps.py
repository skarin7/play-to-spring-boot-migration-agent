"""Gaps learning loop (M6 Task 8): the tool's own blind spots, distinct from
a migration finding.

A finding says "this migration is wrong" -- it belongs to `signature_findings`
(Task 1) or the QA/T4/T5 findings (Task 10/11) and gets fixed inside the run.
A gap says "the tool had no rule, so an agent (or a deterministic node)
improvised" -- it is still true after the run succeeds, because the next Play
repo hits the same wall. `flag_for_manual_review` (tools/fs.py) is a good
*stop signal* but was never a *recorded, aggregatable data point*; this
module is what makes it one.

Lifted from the play-to-springboot plugin's gaps.jsonl design
(docs/GAPS.md): append-only NDJSON (same discipline as Task 7's journal), a
closed `kind` set (free text cannot be counted, which is the whole point of
collecting it), and a `what_i_did` field that is the load-bearing one --
"hand-ported to @Async" is worth a release, "could not find a mapping" is
worth nothing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

GapKind = Literal[
    "unmapped_dependency",
    "unhandled_idiom",
    "tier_blind_spot",
    "tool_error",
    "agent_improvised",
    "layout_surprise",
    "boot_failure",
]

GAP_KINDS: frozenset[str] = frozenset(
    (
        "unmapped_dependency",
        "unhandled_idiom",
        "tier_blind_spot",
        "tool_error",
        "agent_improvised",
        "layout_surprise",
        "boot_failure",
    )
)

Role = Literal["researcher", "architect", "dev", "qa"]


def gaps_path(spring_repo: Path) -> Path:
    return spring_repo / ".migration" / "gaps.jsonl"


def record_gap(
    spring_repo: Path,
    kind: str,
    subject: str,
    what_i_did: str,
    *,
    role: str = "dev",
    blind_tier: str | None = None,
    layer: str | None = None,
) -> str | None:
    """Appends one gap entry. Returns an error string (and does NOT write)
    if `kind` isn't in the closed set -- free text defeats the entire point
    of this being a countable signal, so an invalid kind is rejected rather
    than silently accepted as free-form data. Returns None on success.
    """
    if kind not in GAP_KINDS:
        return f"error: unknown gap kind {kind!r}; must be one of {sorted(GAP_KINDS)}"

    entry: dict[str, Any] = {
        "kind": kind,
        "subject": subject,
        "role": role,
        "what_i_did": what_i_did,
    }
    if blind_tier is not None:
        entry["blind_tier"] = blind_tier
    if layer is not None:
        entry["layer"] = layer

    path = gaps_path(spring_repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write("\n" + json.dumps(entry, default=str))
    return None


def _iter_gap_lines(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    entries: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").split("\n"):
        raw = raw.strip()
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue  # truncated final line -- skipped, not an error (same as journal.py)
        if isinstance(parsed, dict) and "kind" in parsed:
            entries.append(parsed)
    return entries


def read_gaps(spring_repo: Path) -> list[dict[str, Any]]:
    return _iter_gap_lines(gaps_path(spring_repo))
