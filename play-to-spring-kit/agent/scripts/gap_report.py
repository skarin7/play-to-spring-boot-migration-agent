#!/usr/bin/env python3
"""Render (and, on the author's side, aggregate) gaps.jsonl into a readable
report (M6 Task 8).

**No network code in this module at all.** No automatic upload, no automatic
rule promotion -- lifted verbatim as a design constraint from the
play-to-springboot plugin's gap_report.py. What gets shared is a file the
user reads first and decides about themselves.

Redaction policy: framework symbols pass through in the clear; everything
else is hashed with a per-install salt.

  - `play.*`, `org.springframework.*`, `akka.*`, `jakarta.*`, `javax.*` (and
    a few other well-known framework/library root packages) are public
    API -- they belong to the framework, not the user's business, and they
    are what makes a gap actionable to a plugin author who never sees the
    user's source.
  - Everything else (assumed to be the user's own code) is hashed to
    `<class:xxxxxxxx>` (first 8 hex chars of sha256(salt + subject)).

Salt home (Q4 in the original review): this engine has no equivalent of the
Claude Code plugin's `$CLAUDE_PLUGIN_DATA`, so the salt lives in a per-user
config directory instead -- `~/.config/play-to-spring-migration-agent/
install-salt` (or `$XDG_CONFIG_HOME/...` if set), generated once on first
use. Same salt across every run on one machine means "hit this four times"
stays visible within an install; a different salt on a different machine
means the same class name at two installs never collides into one identity
-- matching the plugin's own two stated consequences of per-install salting.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import secrets
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from ..tools.gaps import read_gaps

# Root packages treated as framework/public API -- passed through in the
# clear. Intentionally narrow: an unlisted prefix defaults to redacted,
# which is the safe failure direction (never leak an unrecognized package
# under the assumption it's "probably framework").
_FRAMEWORK_PREFIXES = (
    "play.",
    "org.springframework.",
    "akka.",
    "pekko.",
    "jakarta.",
    "javax.",
    "java.",
    "com.typesafe.",
    "com.fasterxml.",
    "org.apache.",
    "org.junit.",
    "com.google.inject.",
)


def _salt_path() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "play-to-spring-migration-agent" / "install-salt"


def get_or_create_salt() -> str:
    path = _salt_path()
    if path.is_file():
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    salt = secrets.token_hex(16)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(salt, encoding="utf-8")
    return salt


def is_framework_symbol(subject: str) -> bool:
    return any(subject.startswith(prefix) for prefix in _FRAMEWORK_PREFIXES)


def redact_subject(subject: str, salt: str) -> str:
    if is_framework_symbol(subject):
        return subject
    digest = hashlib.sha256((salt + subject).encode("utf-8")).hexdigest()[:8]
    return f"<class:{digest}>"


def redact_gap(gap: dict[str, Any], salt: str) -> dict[str, Any]:
    """Redacts `subject` and, conservatively, `what_i_did` (which may itself
    name user classes) -- but only the subject substring inside it if it
    literally appears there; what_i_did is otherwise left as free text since
    it is meant to be read (this is the load-bearing field), and the user
    reviews the rendered report before sharing it regardless (see the module
    docstring: "the report is a file you read rather than a payload you
    trust")."""
    subject = gap.get("subject", "")
    redacted_subject = redact_subject(subject, salt)
    what_i_did = gap.get("what_i_did", "")
    if subject and not is_framework_symbol(subject) and subject in what_i_did:
        what_i_did = what_i_did.replace(subject, redacted_subject)
    out = dict(gap)
    out["subject"] = redacted_subject
    out["what_i_did"] = what_i_did
    return out


def render(spring_repo: Path) -> str:
    salt = get_or_create_salt()
    gaps = read_gaps(spring_repo)
    redacted = [redact_gap(g, salt) for g in gaps]

    lines = ["# Migration Gaps Report", ""]
    if not redacted:
        lines.append("No gaps recorded for this run.")
        return "\n".join(lines) + "\n"

    by_kind = Counter(g.get("kind", "unknown") for g in redacted)
    lines.append(f"{len(redacted)} gap(s) recorded, {len(by_kind)} distinct kind(s).")
    lines.append("")
    lines.append("| Kind | Count |")
    lines.append("|---|---|")
    for kind, count in by_kind.most_common():
        lines.append(f"| `{kind}` | {count} |")
    lines.append("")

    lines.append("## Detail")
    lines.append("")
    for g in redacted:
        lines.append(f"- **{g.get('kind')}** — `{g.get('subject')}` ({g.get('role', '?')})")
        lines.append(f"  - what happened: {g.get('what_i_did', '')}")
        if g.get("layer"):
            lines.append(f"  - layer: {g['layer']}")
        if g.get("blind_tier"):
            lines.append(f"  - blind tier: {g['blind_tier']}")
    lines.append("")

    lines.append("<details><summary>Full JSON (redacted)</summary>")
    lines.append("")
    lines.append("```json")
    import json

    lines.append(json.dumps(redacted, indent=2, default=str))
    lines.append("```")
    lines.append("</details>")

    return "\n".join(lines) + "\n"


def _cmd_render(args: argparse.Namespace) -> int:
    spring_repo = Path(args.spring_repo).resolve()
    report = render(spring_repo)
    out_path = spring_repo / ".migration" / "gap-report.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    print(f"wrote {out_path}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render/aggregate migration gaps (no network I/O)")
    sub = parser.add_subparsers(dest="command", required=True)

    render_parser = sub.add_parser("render", help="Render one run's gaps.jsonl into gap-report.md")
    render_parser.add_argument("--spring-repo", required=True)
    render_parser.set_defaults(func=_cmd_render)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
