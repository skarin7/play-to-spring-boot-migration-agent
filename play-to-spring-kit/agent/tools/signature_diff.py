"""``java-dev-toolkit`` JAR subprocess wrapper (``signature`` command) + the
pure diff over its output -- the T2 verification tier (M6).

Catches a class made to compile by hollowing it out: a file whose body was
replaced with ``return null;`` still counts as one migrated file, so
file-count verification (``inventory.run_verification``) scores it a
success. Comparing structural signatures (public methods, arity, coarse
return kind) catches it.

JAR contract verified 2026-08-15 against the pinned release
(``scripts/tools/toolkit-release.json`` upstream, ``toolkit-release.json``
here) -- see docs/superpowers/plans/2026-08-15-plugin-parity-hardening.md,
Task 1, "Verified JAR contract". Two points that matter here:

- The ``signature`` subcommand takes a bare positional input plus
  ``-o/--output`` -- *not* ``--source``/``--report`` like ``inventory``.
- A file that fails to parse is reported as ``{"path": ..., "parse_error":
  ...}`` with no ``methods`` key. It must never be read as a class with zero
  methods (that would look like every method vanished); it is excluded from
  the diff and reported as its own finding kind instead.

Mirrors ``toolkit_jar``/``toolkit_inventory``'s injectable-runner pattern so
tests can script subprocess output without a real JAR or ``java`` binary.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .toolkit_jar import RunCmd, run_cmd


def run_signature_scan(
    source_root: Path,
    jar: Path,
    report_path: Path,
    dry_run: bool = False,
    *,
    runner: RunCmd = run_cmd,
) -> dict[str, Any] | None:
    """Invoke ``signature`` on ``source_root`` and read back the JSON it writes.

    Returns ``None`` on dry-run, or if the report is missing/unparseable
    (subprocess failure, no JAR, root doesn't exist) -- callers must treat
    that as "no signal", not fail the run, matching ``run_inventory_scan``.
    """
    argv = ["java", "-jar", str(jar), "signature", str(source_root), "-o", str(report_path)]
    runner(argv, source_root, dry_run)
    if dry_run:
        return None
    if not report_path.is_file():
        return None
    try:
        return json.loads(report_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _public_methods(entry: dict[str, Any]) -> dict[tuple[str, int], dict[str, Any]]:
    """(name, arity) -> method dict, public methods only. Keying on (name, arity)
    lets an overload disappear or change without being confused with its sibling."""
    return {
        (m.get("name", ""), int(m.get("arity", 0))): m
        for m in entry.get("methods", [])
        if m.get("visibility") == "public"
    }


def diff_signatures(
    play_report: dict[str, Any] | None,
    spring_report: dict[str, Any] | None,
    *,
    layer_prefix: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Compare a Play signature report against a Spring one.

    Both reports are keyed by path relative to their own scanned root (see
    module docstring on why the roots must be the language source roots, not
    repo roots, for the keys to line up) -- but the *class name*, not the
    path, is the join key here, since a migrated file's path segment often
    changes (``app/services/Foo.java`` -> ``src/main/java/.../FooService.java``).

    ``layer_prefix``, if given, filters play_report entries to paths starting
    with it (case-sensitive), so a slice-scoped check only looks at classes
    that slice was responsible for.

    Returns three buckets:
      - method_missing: a Play public method with no same-name/arity method
        in the matching Spring class. Blocker.
      - classes_absent_from_spring: a Play class with no matching Spring
        class at all. NOT a finding by itself -- later slices/batches simply
        haven't landed yet. Gating a partially-migrated tree stays safe by
        construction because of this bucket, not despite it.
      - signature_changed: same (name, arity) pair present in both, but
        return-kind bucket differs. Major, not blocker -- a legitimate
        Result -> ResponseEntity rewrite still differs in raw type, but
        returnKind buckets that on purpose (see SignatureExtractor); a
        genuine void<->reference flip is what this actually catches.
      - parse_errors: files either side failed to parse. Reported, never
        silently treated as zero-method classes.
    """
    play_files: dict[str, Any] = (play_report or {}).get("files", {}) or {}
    spring_files: dict[str, Any] = (spring_report or {}).get("files", {}) or {}

    method_missing: list[dict[str, Any]] = []
    classes_absent: list[dict[str, Any]] = []
    signature_changed: list[dict[str, Any]] = []
    parse_errors: list[dict[str, Any]] = []

    spring_by_class: dict[str, dict[str, Any]] = {}
    for path, entry in spring_files.items():
        if not isinstance(entry, dict) or entry.get("parse_error"):
            if isinstance(entry, dict) and entry.get("parse_error"):
                parse_errors.append({"side": "spring", "path": entry.get("path", path), "error": entry["parse_error"]})
            continue
        class_name = entry.get("class") or ""
        if class_name:
            spring_by_class[class_name] = entry

    for path, entry in play_files.items():
        if layer_prefix and not path.startswith(layer_prefix):
            continue
        if not isinstance(entry, dict) or entry.get("parse_error"):
            if isinstance(entry, dict) and entry.get("parse_error"):
                parse_errors.append({"side": "play", "path": entry.get("path", path), "error": entry["parse_error"]})
            continue

        class_name = entry.get("class") or ""
        spring_entry = spring_by_class.get(class_name)
        if spring_entry is None:
            classes_absent.append({"class": class_name, "path": path})
            continue

        play_methods = _public_methods(entry)
        spring_methods = _public_methods(spring_entry)
        for key, play_method in play_methods.items():
            name, arity = key
            spring_method = spring_methods.get(key)
            if spring_method is None:
                method_missing.append(
                    {
                        "class": class_name,
                        "method": name,
                        "arity": arity,
                        "play_path": path,
                    }
                )
                continue
            if play_method.get("returns") != spring_method.get("returns"):
                signature_changed.append(
                    {
                        "class": class_name,
                        "method": name,
                        "arity": arity,
                        "play_returns": play_method.get("returns"),
                        "spring_returns": spring_method.get("returns"),
                    }
                )

    return {
        "method_missing": method_missing,
        "classes_absent_from_spring": classes_absent,
        "signature_changed": signature_changed,
        "parse_errors": parse_errors,
    }
