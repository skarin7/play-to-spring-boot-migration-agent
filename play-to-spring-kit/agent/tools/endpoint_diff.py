"""T5 endpoint parity (M6 Task 10): does the migrated app return the same
thing the Play app did, not just "does it compile / keep its methods / answer
at the right path" (T1/T2/T3).

Three deterministic, pure functions -- no subprocess, no LLM, testable with
plain dicts:

  - `build_probes(routes)`   -- seed probes from a parsed conf/routes list
  - `capture(base_url, probes, http_get)` -- one HTTP GET per probe (http_get
    is the injectable seam, same convention as every other `runner`/`popen_factory`
    in tools/)
  - `diff_responses(before, after)` -- the actual comparison

**Parameterless GET routes only, by default.** POST/PUT/PATCH/DELETE need a
request body (conf/routes does not record one) and identical starting state
in both apps (the first capture changes what the second reads) -- neither is
available here, so mutating verbs are recorded as `unproved`, never silently
skipped and never scored as a pass. Path-parameterized routes (":id") are
also skipped by build_probes by default, since there's no sample value to
plug in without either a fixture file or another LLM round -- an empty
`path_params` mapping is the honest "we don't have this" state, not a bug.

Comparison policy: timestamps/ids/durations differ between any two runs of
the same app, so those are compared for presence and type, not equality.
Field ordering in a JSON body is never a difference (dict key order is not
semantically meaningful).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

# Route field names that vary run-to-run even for a byte-identical app, so
# equality would produce noise instead of signal. Matched at any nesting
# depth against a JSON body's key names.
#
# "id" must be its own camelCase/snake_case word: exactly "id", "Id"
# following a lowercase letter (the camelCase boundary in "userId"), or "id"
# following an underscore ("order_id") -- kept CASE-SENSITIVE deliberately,
# since case-insensitive matching is exactly what would make "grid"/"valid"
# false-positive (no case transition, no separator precedes "id" in either).
# The rest (timestamp/duration/expires/created·at/updated·at) are plain
# case-insensitive substring matches, so "durationMs"/"expiresAt" match
# regardless of what surrounds the volatile word.
_ID_FIELD_RE = re.compile(r"^id$|(?<=[a-z])Id$|_id$")
_OTHER_VOLATILE_RE = re.compile(r"(timestamp|duration|expires|created.?at|updated.?at)", re.IGNORECASE)


def _is_volatile_field_name(leaf_name: str) -> bool:
    return bool(_ID_FIELD_RE.search(leaf_name)) or bool(_OTHER_VOLATILE_RE.search(leaf_name))

PATH_PARAM_RE = re.compile(r":(\w+)")

HttpGet = Callable[[str], "HttpResponse"]


@dataclass
class HttpResponse:
    status: int
    body: str
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class Probe:
    method: str
    path: str
    resolved_path: str  # path params substituted in, if any


def build_probes(
    routes: list[dict[str, Any]],
    *,
    path_params: dict[str, str] | None = None,
    include_mutating: bool = False,
) -> tuple[list[Probe], list[dict[str, Any]]]:
    """Returns (probes, unproved). `unproved` entries are routes this pass
    could not safely probe -- a mutating verb with include_mutating=False, or
    a path parameter with no sample value in `path_params` -- each carrying a
    `reason` so the caller can report them as unproved, never as a silent
    drop or a false pass.
    """
    path_params = path_params or {}
    probes: list[Probe] = []
    unproved: list[dict[str, Any]] = []

    for route in routes:
        method = (route.get("method") or "").upper()
        path = route.get("path") or ""

        if method != "GET" and not include_mutating:
            unproved.append(
                {**route, "reason": f"{method} needs a request body and identical starting state; not probed"}
            )
            continue

        param_names = PATH_PARAM_RE.findall(path)
        if param_names:
            missing = [p for p in param_names if p not in path_params]
            if missing:
                unproved.append({**route, "reason": f"no sample value for path param(s): {missing}"})
                continue
            resolved = path
            for name in param_names:
                resolved = resolved.replace(f":{name}", path_params[name])
        else:
            resolved = path

        probes.append(Probe(method=method, path=path, resolved_path=resolved))

    return probes, unproved


def capture(base_url: str, probes: list[Probe], http_get: HttpGet) -> dict[str, HttpResponse]:
    """One capture per probe, keyed by resolved_path. A probe whose request
    itself raises (connection refused, timeout) is recorded with status -1
    and the exception text as the body -- never silently dropped, since a
    dropped probe would make its absence from the diff look like agreement."""
    results: dict[str, HttpResponse] = {}
    for probe in probes:
        url = base_url.rstrip("/") + probe.resolved_path
        try:
            results[probe.resolved_path] = http_get(url)
        except Exception as exc:  # noqa: BLE001 -- capture must never abort the whole run
            results[probe.resolved_path] = HttpResponse(status=-1, body=str(exc))
    return results


def _parse_json(body: str) -> Any:
    try:
        return json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return None


def _coarse_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unknown"


def _diff_json_value(path: str, before: Any, after: Any, findings: list[dict[str, Any]]) -> None:
    """Recursive structural diff. Dict key order is never compared (Python
    dict equality already ignores it; we additionally never touch key
    ORDER anywhere in this function, only key SETS and values). A key whose
    name looks volatile (id/timestamp/duration/...) is checked for presence
    and coarse type only, never exact equality."""
    if isinstance(before, dict) and isinstance(after, dict):
        before_keys, after_keys = set(before), set(after)
        for missing in sorted(before_keys - after_keys):
            findings.append({"path": f"{path}.{missing}", "kind": "field_missing_in_after"})
        for extra in sorted(after_keys - before_keys):
            findings.append({"path": f"{path}.{extra}", "kind": "field_added_in_after"})
        for key in sorted(before_keys & after_keys):
            _diff_json_value(f"{path}.{key}", before[key], after[key], findings)
        return

    if isinstance(before, list) and isinstance(after, list):
        if len(before) != len(after):
            findings.append(
                {"path": path, "kind": "array_length_changed", "before": len(before), "after": len(after)}
            )
            return
        for i, (b_item, a_item) in enumerate(zip(before, after)):
            _diff_json_value(f"{path}[{i}]", b_item, a_item, findings)
        return

    leaf_name = path.rsplit(".", 1)[-1].split("[")[0]
    if _is_volatile_field_name(leaf_name):
        if _coarse_type(before) != _coarse_type(after):
            findings.append(
                {"path": path, "kind": "volatile_field_type_changed", "before_type": _coarse_type(before),
                 "after_type": _coarse_type(after)}
            )
        return

    if before != after:
        findings.append({"path": path, "kind": "value_changed", "before": before, "after": after})


def diff_responses(before: dict[str, HttpResponse], after: dict[str, HttpResponse]) -> list[dict[str, Any]]:
    """One finding entry per probed path with a difference. A path present
    in `before` but missing from `after` (or vice versa) is its own finding
    kind, not silently skipped."""
    findings: list[dict[str, Any]] = []
    all_paths = sorted(set(before) | set(after))

    for path in all_paths:
        b = before.get(path)
        a = after.get(path)
        if b is None or a is None:
            findings.append({"path": path, "kind": "probe_missing_on_one_side"})
            continue
        if b.status != a.status:
            findings.append({"path": path, "kind": "status_changed", "before": b.status, "after": a.status})
            continue
        if b.status < 0 or a.status < 0:
            # A failed request (connection refused, timeout) with matching
            # negative statuses -- both sides failed the same way, worth a
            # finding but not worth a deep body diff of two error strings.
            findings.append({"path": path, "kind": "both_sides_unreachable"})
            continue

        b_json, a_json = _parse_json(b.body), _parse_json(a.body)
        if b_json is None and a_json is None:
            if b.body != a.body:
                findings.append({"path": path, "kind": "body_changed_non_json"})
            continue
        if (b_json is None) != (a_json is None):
            findings.append({"path": path, "kind": "body_content_type_changed"})
            continue

        body_findings: list[dict[str, Any]] = []
        _diff_json_value(path, b_json, a_json, body_findings)
        findings.extend(body_findings)

    return findings
