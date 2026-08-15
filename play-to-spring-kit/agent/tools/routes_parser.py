"""Play ``conf/routes`` parsing + diff against existing Spring ``@*Mapping`` annotations.

Deterministic, pure (no subprocess, no LLM) — feeds the M4 routes agent
(agent/agents/routes.py) the list of routes that still need a Spring mapping.

Not a full Java/routes parser (same pragmatism as tools/toolkit_jar.py's
regex-based subprocess-output parsing): a route line is 3 whitespace-separated
fields (method, url path, ``package.Controller.action(args)`` call spec);
Spring annotations are found by regex-scanning source lines for an
``@XxxMapping`` immediately preceding a method declaration.
"""

from __future__ import annotations

import re
from pathlib import Path

MAPPING_ANNOTATION_RE = re.compile(
    r"@(?:GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping|RequestMapping)\b"
)
QUOTED_STRING_RE = re.compile(r'"([^"]*)"')
CLASS_DECL_RE = re.compile(r"\bclass\s+(\w+)")
METHOD_DECL_RE = re.compile(r"\b(?:public|private|protected)\s+[\w<>\[\],.?\s]+?\s+(\w+)\s*\(")
LOOKAHEAD_LINES = 5


def parse_routes_file(path: Path) -> list[dict]:
    """Parse a Play ``conf/routes`` file into a list of route dicts.

    Blank lines and ``#``-comments are skipped. Malformed lines (not exactly
    3 whitespace-separated fields, or a call spec with no ``(``) are skipped,
    not raised.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    routes: list[dict] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 2)
        if len(parts) != 3:
            continue
        method, url_path, call_spec = parts
        paren_idx = call_spec.find("(")
        if paren_idx == -1:
            continue
        fq_action = call_spec[:paren_idx].strip()
        if "." not in fq_action:
            continue
        params = call_spec[paren_idx + 1 :].rstrip()
        if params.endswith(")"):
            params = params[:-1]
        controller, action = fq_action.rsplit(".", 1)
        routes.append(
            {
                "method": method,
                "path": url_path,
                "controller": controller,
                "action": action,
                "params": params,
            }
        )
    return routes


def find_spring_mappings(spring_repo: Path) -> dict[str, set[str]]:
    """Scan Spring controller sources for existing ``@*Mapping`` annotations.

    Returns ``{"ClassName.methodName": {path, ...}}`` for every method that
    already has a route-mapping annotation with at least one literal path.

    Class nesting is tracked with a brace-depth stack (not a full parser: it
    assumes the common one-class-declaration-per-line, brace-on-same-line
    style), so a nested ``static`` DTO class declared earlier in the file
    doesn't shadow the enclosing controller class for methods declared after
    it back at the outer level.
    """
    java_root = spring_repo / "src" / "main" / "java"
    mappings: dict[str, set[str]] = {}
    if not java_root.is_dir():
        return mappings

    for java_file in java_root.rglob("*.java"):
        lines = java_file.read_text(encoding="utf-8", errors="replace").splitlines()
        depth = 0
        class_stack: list[tuple[int, str]] = []  # (brace_depth_of_body, name)
        for i, line in enumerate(lines):
            depth += line.count("{") - line.count("}")
            class_match = CLASS_DECL_RE.search(line)
            if class_match:
                class_stack.append((depth, class_match.group(1)))
            while class_stack and depth < class_stack[-1][0]:
                class_stack.pop()
            current_class = class_stack[-1][1] if class_stack else None

            if not MAPPING_ANNOTATION_RE.search(line):
                continue
            paths = QUOTED_STRING_RE.findall(line)
            if not paths or current_class is None:
                continue
            method_name = None
            for j in range(i + 1, min(i + 1 + LOOKAHEAD_LINES, len(lines))):
                method_match = METHOD_DECL_RE.search(lines[j])
                if method_match:
                    method_name = method_match.group(1)
                    break
            if method_name is None:
                continue
            key = f"{current_class}.{method_name}"
            mappings.setdefault(key, set()).update(paths)
    return mappings


def diff_routes(
    routes: list[dict], spring_mappings: dict[str, set[str]]
) -> tuple[list[dict], list[dict]]:
    """Split ``routes`` into (mapped, unmapped) against ``spring_mappings``."""
    mapped: list[dict] = []
    unmapped: list[dict] = []
    for route in routes:
        controller_simple = route["controller"].rsplit(".", 1)[-1]
        key = f"{controller_simple}.{route['action']}"
        paths = spring_mappings.get(key)
        if paths is not None and route["path"] in paths:
            mapped.append(route)
        else:
            unmapped.append(route)
    return mapped, unmapped
