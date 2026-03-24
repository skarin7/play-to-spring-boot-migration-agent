#!/usr/bin/env python3
"""
Flatten Play Framework HOCON (application.conf) into Spring Boot application.properties.

Requires: pyhocon. Prefer the kit venv: run repo ``./start_upgrade.sh`` or
``python3 -m venv play-to-spring-kit/.venv`` and ``pip install -r scripts/requirements-venv.txt``.
See also ``requirements-conf.txt`` for system installs (PEP 668 may need ``--break-system-packages``).

Resolves HOCON includes relative to the main file. Outputs dot-flattened keys suitable
for PhenomConfig / Spring Environment (manual review still needed: play.* → Spring keys).

Examples:
  python3 scripts/conf_to_application_properties.py \\
    --input ../my-play-app/conf/application.conf \\
    --output ../spring-my-app/src/main/resources/application.properties

  python3 scripts/conf_to_application_properties.py -i conf/application.conf -o - \\
    --strip-prefix play. --strip-prefix akka.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    from pyhocon import ConfigFactory, ConfigTree
except ImportError:
    print(
        "ERROR: pyhocon is required. Options:\n"
        "  • From monorepo root: ./start_upgrade.sh … (uses play-to-spring-kit/.venv)\n"
        "  • Manual venv: cd play-to-spring-kit && python3 -m venv .venv && "
        ".venv/bin/pip install -r scripts/requirements-venv.txt\n"
        "  • System pip (may need --break-system-packages on PEP 668): "
        "python3 -m pip install -r scripts/requirements-conf.txt",
        file=sys.stderr,
    )
    sys.exit(2)


def _is_config_tree(obj: Any) -> bool:
    return isinstance(obj, ConfigTree)


def flatten_hocon(obj: Any, prefix: str, out: Dict[str, Any]) -> None:
    """Recursively flatten ConfigTree / dict-like into dot keys."""
    if obj is None:
        return
    if _is_config_tree(obj) or isinstance(obj, dict):
        if isinstance(obj, dict) and not _is_config_tree(obj):
            items = obj.items()
        else:
            items = obj.items()
        for k, v in items:
            key = f"{prefix}.{k}" if prefix else str(k)
            if _is_config_tree(v) or (isinstance(v, dict) and not isinstance(v, str)):
                flatten_hocon(v, key, out)
            else:
                out[key] = v
        return
    # Leaf at root (unusual)
    if not prefix:
        return
    out[prefix] = obj


def format_scalar_for_list(x: Any) -> str:
    if x is None:
        return ""
    if isinstance(x, bool):
        return "true" if x else "false"
    return str(x)


def to_spring_value(value: Any) -> Optional[str]:
    """Convert a HOCON value to a single-line Spring property string."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        # HOCON may use duration/size types — pyhocon often gives int/float/str
        return str(value)
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        if len(value) == 0:
            return ""
        if all(
            isinstance(x, (str, int, float, bool)) or x is None for x in value
        ):
            parts = []
            for x in value:
                if x is None:
                    continue
                if isinstance(x, bool):
                    parts.append("true" if x else "false")
                else:
                    s = str(x).strip()
                    if "," in s or '"' in s:
                        parts.append(json.dumps(s)[1:-1])  # unquoted inner? simpler: json each
                    else:
                        parts.append(str(x))
            return ",".join(parts)
        return json.dumps(value, separators=(",", ":"))
    if _is_config_tree(value):
        nested: Dict[str, Any] = {}
        flatten_hocon(value, "", nested)
        return json.dumps(nested, separators=(",", ":"))
    if isinstance(value, dict):
        return json.dumps(value, separators=(",", ":"))
    return str(value)


def escape_property_value(raw: str) -> str:
    """Escape a value for Java .properties (Spring-compatible)."""
    if raw is None:
        return ""
    if raw == "":
        return ""
    # Unescaped leading/trailing space, #, !, =, :, or contains whitespace → quote
    needs_quote = bool(
        re.search(r"[\s=#:!\\]", raw)
        or raw != raw.strip()
        or raw.startswith(" ")
        or raw.endswith(" ")
    )
    if not needs_quote:
        return raw
    esc = (
        raw.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{esc}"'


def should_emit_key(key: str, strip_prefixes: Tuple[str, ...]) -> bool:
    for p in strip_prefixes:
        if p and (key == p.rstrip(".") or key.startswith(p)):
            return False
    return True


def convert(
    input_path: Path,
    strip_prefixes: Tuple[str, ...],
    extra_header: str,
) -> str:
    conf = ConfigFactory.parse_file(str(input_path.resolve()))
    flat: Dict[str, Any] = {}
    flatten_hocon(conf, "", flat)

    lines: List[str] = []
    lines.append("# Generated from Play HOCON — review before production.")
    lines.append(f"# Source: {input_path}")
    lines.append("# Map domain keys to Spring (e.g. mongodb.uri → spring.data.mongodb.uri).")
    if extra_header:
        for hline in extra_header.strip().splitlines():
            lines.append(f"# {hline}")
    lines.append("")

    items = sorted(flat.items(), key=lambda kv: kv[0].lower())
    for k, v in items:
        if not should_emit_key(k, strip_prefixes):
            continue
        sv = to_spring_value(v)
        if sv is None:
            continue
        lines.append(f"{k}={escape_property_value(sv)}")

    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert Play application.conf (HOCON) to Spring application.properties.",
    )
    parser.add_argument(
        "-i",
        "--input",
        type=Path,
        required=True,
        help="Path to application.conf (HOCON). Includes are resolved relative to this file.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        default=None,
        help="Output file path, or '-' for stdout (default: stdout).",
    )
    parser.add_argument(
        "--strip-prefix",
        action="append",
        default=[],
        metavar="PREFIX",
        help=(
            "Omit keys equal to or starting with this prefix (repeatable). "
            "Example: --strip-prefix play. --strip-prefix akka."
        ),
    )
    parser.add_argument(
        "--note",
        default="",
        help="Extra comment block (single string) inserted under the header.",
    )
    args = parser.parse_args()

    inp = args.input
    if not inp.is_file():
        print(f"ERROR: input not found: {inp}", file=sys.stderr)
        return 1

    # Normalize strip prefixes (ensure trailing dot for startswith)
    normalized: List[str] = []
    for p in args.strip_prefix or []:
        p = p.strip()
        if not p:
            continue
        if not p.endswith("."):
            p = p + "."
        normalized.append(p)

    body = convert(inp, tuple(normalized), args.note)

    out_spec = args.output
    if out_spec is None or out_spec == "-":
        sys.stdout.write(body)
        return 0

    outp = Path(out_spec)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(body, encoding="utf-8")
    print(f"Wrote {outp} ({len(body)} bytes)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
