#!/usr/bin/env python3
"""
Flatten Play Framework HOCON (application.conf) into Spring Boot application.properties.

Requires: pyhocon. Prefer the kit venv: run repo ``./start_upgrade.sh`` or
``python3 -m venv play-to-spring-kit/.venv`` and ``pip install -r scripts/requirements-venv.txt``.

Handles:
  - Nested objects  → flattened to dot-keys  (db.mongo.uri=...)
  - Scalar lists    → comma-separated         (allowed.hosts=a,b,c)
  - Object lists    → Spring indexed keys     (servers[0].host=a  servers[0].port=8080)
  - Boolean/number  → string representation
  - Special chars   → backslash-escaped per Java .properties spec (no double-quoting)

Examples:
  python3 scripts/conf_to_application_properties.py \\
    --input ../my-play-app/conf/application.conf \\
    --output ../spring-my-app/src/main/resources/application.properties

  python3 scripts/conf_to_application_properties.py -i conf/application.conf -o - \\
    --strip-prefix play. --strip-prefix akka.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_obj(v: Any) -> bool:
    return isinstance(v, (ConfigTree, dict))


def _is_all_scalars(lst: list) -> bool:
    return all(isinstance(x, (str, int, float, bool)) or x is None for x in lst)


# ---------------------------------------------------------------------------
# Flattening
# ---------------------------------------------------------------------------

def flatten(obj: Any, prefix: str, out: Dict[str, Any]) -> None:
    """
    Recursively flatten a HOCON ConfigTree into dot-separated property keys.

    Scalar leaves      → out[key] = value
    Nested objects     → recurse with extended prefix
    Scalar lists       → out[key] = [v1, v2, ...]  (serialized as comma-string)
    Object lists       → expand to indexed keys: prefix[0].field, prefix[1].field, ...
                         This is what Spring @ConfigurationProperties expects.
    """
    if obj is None:
        return

    if _is_obj(obj):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            if _is_obj(v):
                flatten(v, key, out)
            elif isinstance(v, list):
                _flatten_list(v, key, out)
            else:
                out[key] = v
        return

    if prefix:
        out[prefix] = obj


def _flatten_list(lst: list, prefix: str, out: Dict[str, Any]) -> None:
    if not lst:
        out[prefix] = []
        return

    if _is_all_scalars(lst):
        # Scalar list — keep as single entry, serialized to comma-string later
        out[prefix] = lst
        return

    # Object list — expand to Spring indexed keys so @ConfigurationProperties can bind them
    for i, item in enumerate(lst):
        key = f"{prefix}[{i}]"
        if _is_obj(item):
            flatten(item, key, out)
        elif isinstance(item, list):
            _flatten_list(item, key, out)
        else:
            out[key] = item


# ---------------------------------------------------------------------------
# Value serialization
# ---------------------------------------------------------------------------

def to_spring_value(value: Any) -> Optional[str]:
    """Convert a flattened HOCON value to a .properties string."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        if not value:
            return ""
        parts = []
        for x in value:
            if x is None:
                continue
            s = "true" if x is True else ("false" if x is False else str(x))
            # Escape commas that are part of the value itself
            parts.append(s.replace(",", "\\,"))
        return ",".join(parts)
    # Fallback for anything unexpected
    return str(value)


def escape_value(raw: str) -> str:
    """
    Escape a value for Java .properties format.

    Java .properties does NOT support double-quoted values — quotes are literal characters.
    Special characters are backslash-escaped per the Properties spec.
    """
    if not raw:
        return raw
    return (
        raw.replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

def should_emit(key: str, strip_prefixes: Tuple[str, ...]) -> bool:
    for p in strip_prefixes:
        if p and (key == p.rstrip(".") or key.startswith(p)):
            return False
    return True


# ---------------------------------------------------------------------------
# Main conversion
# ---------------------------------------------------------------------------

def convert(input_path: Path, strip_prefixes: Tuple[str, ...], extra_header: str) -> str:
    conf = ConfigFactory.parse_file(str(input_path.resolve()))
    flat: Dict[str, Any] = {}
    flatten(conf, "", flat)

    lines: List[str] = [
        "# Generated from Play HOCON — review before production.",
        f"# Source: {input_path}",
        "# Map domain keys to Spring (e.g. mongodb.uri -> spring.data.mongodb.uri).",
        "# Object lists are expanded to indexed keys (key[0].field=val) for @ConfigurationProperties.",
    ]
    if extra_header:
        for hline in extra_header.strip().splitlines():
            lines.append(f"# {hline}")
    lines.append("")

    for k, v in sorted(flat.items(), key=lambda kv: kv[0].lower()):
        if not should_emit(k, strip_prefixes):
            continue
        sv = to_spring_value(v)
        if sv is None:
            continue
        lines.append(f"{k}={escape_value(sv)}")

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert Play application.conf (HOCON) to Spring application.properties.",
    )
    parser.add_argument("-i", "--input", type=Path, required=True,
                        help="Path to application.conf. Includes resolved relative to this file.")
    parser.add_argument("-o", "--output", type=str, default=None,
                        help="Output path, or '-' for stdout (default: stdout).")
    parser.add_argument("--strip-prefix", action="append", default=[], metavar="PREFIX",
                        help="Omit keys starting with PREFIX (repeatable). E.g. --strip-prefix play.")
    parser.add_argument("--note", default="", help="Extra comment inserted under the header.")
    args = parser.parse_args()

    if not args.input.is_file():
        print(f"ERROR: input not found: {args.input}", file=sys.stderr)
        return 1

    normalized: List[str] = []
    for p in args.strip_prefix or []:
        p = p.strip()
        if p and not p.endswith("."):
            p += "."
        if p:
            normalized.append(p)

    body = convert(args.input, tuple(normalized), args.note)

    if args.output is None or args.output == "-":
        sys.stdout.write(body)
        return 0

    outp = Path(args.output)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(body, encoding="utf-8")
    print(f"Wrote {outp} ({len(body)} bytes)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
