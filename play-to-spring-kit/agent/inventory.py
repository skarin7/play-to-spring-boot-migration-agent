"""Deterministic Play-source inventory and migration-unit discovery.

Copied from scripts/migration_orchestrator.py (same rationale as legacy_logic.py:
decouple the graph engine from the 2900-line legacy module while keeping exact
byte-parity semantics):
  LAYER_ORDER / classify_layer          (orchestrator :497, :535)
  scan_play_java / scan_spring_java     (orchestrator :551, :580)
  normalize_path_prefix                 (orchestrator :602)
  discover_migration_units + helpers    (orchestrator :616-807)
  default_unit_entry / merge_discovered_migration_units (orchestrator :810-851)
  expected_play_java_for_slice / migration_output_plausible (orchestrator :1928-1993)
  run_verification                      (orchestrator :2038)
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LAYER_ORDER = ("model", "repository", "manager", "service", "controller", "other")

CONTROLLER_DIR_NAMES = frozenset({"controllers", "controller"})


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def classify_layer(relative_path: str) -> str:
    """LayerDetector-aligned order: controller -> service -> model -> manager -> repository -> other."""
    p = relative_path.replace("\\", "/").lower()
    if "/controllers/" in p:
        return "controller"
    if "/service/" in p or "/services/" in p:
        return "service"
    if "/models/" in p or p.endswith("model.java"):
        return "model"
    if "/db/" in p:
        return "manager"
    if "/repositories/" in p or "/dao/" in p:
        return "repository"
    return "other"


def scan_play_java(play_root: Path, java_subdir: str = "app") -> dict[str, Any]:
    root = play_root / java_subdir
    by_layer: dict[str, int] = {k: 0 for k in LAYER_ORDER}
    total = 0
    if not root.is_dir():
        return {
            "captured_at": iso_now(),
            "play_java_root": java_subdir,
            "total_java_files": 0,
            "by_layer": by_layer,
        }
    for f in root.rglob("*.java"):
        if not f.is_file():
            continue
        try:
            rel = f.relative_to(play_root)
        except ValueError:
            rel = f
        layer = classify_layer(str(rel))
        by_layer[layer] = by_layer.get(layer, 0) + 1
        total += 1
    return {
        "captured_at": iso_now(),
        "play_java_root": java_subdir,
        "total_java_files": total,
        "by_layer": by_layer,
    }


def scan_spring_java(spring_root: Path) -> tuple[int, dict[str, int]]:
    src = spring_root / "src" / "main" / "java"
    by_layer: dict[str, int] = {k: 0 for k in LAYER_ORDER}
    total = 0
    if not src.is_dir():
        return 0, by_layer
    for f in src.rglob("*.java"):
        if not f.is_file():
            continue
        try:
            rel = f.relative_to(src)
        except ValueError:
            rel = f
        layer = classify_layer(str(rel))
        by_layer[layer] = by_layer.get(layer, 0) + 1
        total += 1
    return total, by_layer


def normalize_path_prefix(raw: str) -> str:
    """Match dev-toolkit: relative to app/, forward slashes, trim slashes."""
    if not raw:
        return ""
    s = raw.strip().replace("\\", "/")
    while s.startswith("./"):
        s = s[2:]
    while s.startswith("/"):
        s = s[1:]
    while s.endswith("/") and len(s) > 1:
        s = s[:-1]
    return s


def _count_java_files(root: Path) -> int:
    if not root.is_dir():
        return 0
    return sum(1 for p in root.rglob("*.java") if p.is_file())


def _immediate_subdirs(path: Path) -> list[Path]:
    if not path.is_dir():
        return []
    return sorted((p for p in path.iterdir() if p.is_dir()), key=lambda p: p.name.lower())


def _rel_posix_from_app(app_dir: Path, p: Path) -> str:
    return p.resolve().relative_to(app_dir.resolve()).as_posix()


def _units_from_parent_p(app_dir: Path, parent_p: Path) -> list[str]:
    """One path_prefix per immediate child dir with Java; optional prefix for .java files directly in parent_p."""
    prefixes: list[str] = []
    for d in _immediate_subdirs(parent_p):
        if _count_java_files(d) > 0:
            prefixes.append(_rel_posix_from_app(app_dir, d))
    loose = [f for f in parent_p.glob("*.java") if f.is_file()]
    if loose:
        root_px = _rel_posix_from_app(app_dir, parent_p)
        if root_px not in prefixes:
            prefixes.append(root_px)
    return sorted(set(prefixes))


def _score_split_parent(app_dir: Path, parent_p: Path) -> tuple[int, int, int]:
    subs = _immediate_subdirs(parent_p)
    with_java = sum(1 for s in subs if _count_java_files(s) > 0)
    total = _count_java_files(parent_p)
    depth = len(parent_p.resolve().relative_to(app_dir.resolve()).parts)
    return (with_java, total, depth)


def _find_controller_anchor_parents(app_dir: Path) -> list[Path]:
    """Directories P such that P/controllers or P/controller exists."""
    found: list[Path] = []
    seen: set[str] = set()
    for dirpath, dirnames, _filenames in os.walk(app_dir, topdown=True):
        root_p = Path(dirpath)
        lower_map = {d.lower(): d for d in dirnames}
        for cn in CONTROLLER_DIR_NAMES:
            if cn in lower_map:
                child = root_p / lower_map[cn]
                if child.is_dir():
                    key = str(root_p.resolve())
                    if key not in seen:
                        seen.add(key)
                        found.append(root_p)
                break
    return found


def _pick_best_parent(app_dir: Path, candidates: list[Path]) -> Path | None:
    if not candidates:
        return None
    best: Path | None = None
    best_key: tuple[int, int, int] | None = None
    for p in candidates:
        k = _score_split_parent(app_dir, p)
        if best is None or k > best_key:  # type: ignore[operator]
            best = p
            best_key = k
    return best


def _fallback_branch_parent(app_dir: Path) -> Path | None:
    """Descend through <=2-wide chains until a directory has >=3 subdirs."""
    cur = app_dir.resolve()
    app_res = app_dir.resolve()
    while True:
        subs = _immediate_subdirs(cur)
        if len(subs) >= 3:
            return cur
        if len(subs) == 0:
            return None
        if len(subs) == 1:
            cur = subs[0]
            continue
        subs.sort(key=lambda s: (-_count_java_files(s), s.as_posix().lower()))
        cur = subs[0]
        if not str(cur.resolve()).startswith(str(app_res)):
            return None


def discover_migration_units(
    play_root: Path,
    *,
    java_subdir: str = "app",
    migration_unit_root: str | None = None,
) -> list[dict[str, Any]]:
    """
    Filesystem-derived migration units: path_prefix relative to play ``app/``.
    Controllers-folder anchor, >=3-subdir fallback, optional workspace override.
    """
    app_dir = (play_root / java_subdir).resolve()
    if not app_dir.is_dir():
        return [{"id": "app_root", "path_prefix": "", "java_file_count": 0, "discovered_by": "empty_app"}]

    forced = normalize_path_prefix((migration_unit_root or "").strip())
    if forced:
        p_forced = (app_dir / forced.replace("/", os.sep)).resolve()
        try:
            p_forced.relative_to(app_dir)
        except ValueError:
            p_forced = app_dir
        if p_forced.is_dir():
            prefs = _units_from_parent_p(app_dir, p_forced)
            if not prefs:
                prefs = [forced] if _count_java_files(p_forced) > 0 else []
            if not prefs:
                prefs = [""]
            out = []
            for px in prefs:
                cnt = _count_java_files(app_dir / px.replace("/", os.sep)) if px else _count_java_files(app_dir)
                uid = (px or "app_root").replace("/", "_").replace("\\", "_")
                out.append({"id": uid, "path_prefix": px, "java_file_count": cnt, "discovered_by": "migration_unit_root"})
            return out

    triggers = _find_controller_anchor_parents(app_dir)
    best_p = _pick_best_parent(app_dir, triggers)
    if best_p is not None:
        prefs = _units_from_parent_p(app_dir, best_p)
        if prefs:
            out = []
            for px in prefs:
                cnt = _count_java_files(app_dir / px.replace("/", os.sep))
                uid = px.replace("/", "_").replace("\\", "_") if px else "app_root"
                out.append({"id": uid, "path_prefix": px, "java_file_count": cnt, "discovered_by": "controllers_anchor"})
            return out

    branch = _fallback_branch_parent(app_dir)
    if branch is not None:
        prefs = []
        for d in _immediate_subdirs(branch):
            if _count_java_files(d) > 0:
                prefs.append(_rel_posix_from_app(app_dir, d))
        loose = [f for f in branch.glob("*.java") if f.is_file()]
        if loose:
            rp = _rel_posix_from_app(app_dir, branch)
            if rp not in prefs:
                prefs.append(rp)
        prefs = sorted(set(prefs))
        if prefs:
            out = []
            for px in prefs:
                cnt = _count_java_files(app_dir / px.replace("/", os.sep))
                uid = px.replace("/", "_").replace("\\", "_")
                out.append({"id": uid, "path_prefix": px, "java_file_count": cnt, "discovered_by": "three_plus_subdirs"})
            return out

    total = _count_java_files(app_dir)
    return [{"id": "app_root", "path_prefix": "", "java_file_count": total, "discovered_by": "whole_app"}]


def default_unit_entry(unit_id: str, path_prefix: str, java_file_count: int) -> dict[str, Any]:
    return {
        "id": unit_id,
        "path_prefix": path_prefix,
        "java_file_count": int(java_file_count),
        "status": "pending",
        "retry_count": 0,
        "llm_calls": 0,
        "files_migrated": 0,
        "validate_iteration": 0,
        "last_error_count": None,
        "failure_reason": None,
        "error_fingerprints": [],
        "det_fix_log": [],
    }


def merge_discovered_migration_units(
    existing: list[dict[str, Any]] | None,
    discovered: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Preserve per-unit progress; refresh path_prefix/java_file_count from disk."""
    by_id: dict[str, dict[str, Any]] = {}
    for u in existing or []:
        uid = u.get("id")
        if uid:
            by_id[str(uid)] = dict(u)
    out: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for d in discovered:
        uid = str(d["id"])
        seen_ids.add(uid)
        fresh = default_unit_entry(uid, str(d.get("path_prefix", "")), int(d.get("java_file_count", 0)))
        if uid in by_id:
            old = by_id[uid]
            merged = {**fresh, **old}
            merged["path_prefix"] = fresh["path_prefix"]
            merged["java_file_count"] = fresh["java_file_count"]
            merged["id"] = uid
            out.append(merged)
        else:
            out.append(fresh)
    for uid, old in by_id.items():
        if uid not in seen_ids and old.get("status") == "done":
            out.append(old)
    return sorted(out, key=lambda u: (u.get("path_prefix") or ""))


def expected_play_java_for_slice(label: str, unit: dict[str, Any], source_inventory: dict[str, Any] | None) -> int:
    """How many Java files Play should contribute for this slice (path-unit mode: unit metadata)."""
    return int(unit.get("java_file_count", 0) or 0)


def migration_output_plausible(
    *,
    dry_run: bool,
    label: str,
    unit: dict[str, Any],
    source_inventory: dict[str, Any] | None,
    spring_repo: Path,
    n_add: int,
) -> tuple[bool, str]:
    """
    True if we should allow marking the slice done when ``mvn compile`` succeeds.

    Otherwise an empty Spring scaffold (e.g. only ``Application.java``) compiles
    while migrate-app wrote 0 files -- a common false "done".
    """
    if dry_run:
        return True, ""
    exp = expected_play_java_for_slice(label, unit, source_inventory)
    if exp <= 0:
        return True, ""
    cumulative = int(unit.get("files_migrated", 0) or 0)
    if n_add > 0 or cumulative > 0:
        return True, ""
    total_sp, _by_sp = scan_spring_java(spring_repo)
    inv = source_inventory or {}
    inv_total = int(inv.get("total_java_files") or 0)
    return (
        False,
        f"path unit {label!r} expects ~{exp} Java files under Play app/, but migrate-app wrote 0 "
        f"this round and files_migrated is still 0 (Spring main/java has {total_sp} files; "
        f"inventory total_java_files={inv_total}). "
        f"Compile can succeed with only the bootstrap app class. "
        f"Check slice not skipped as done, path-prefix mismatch, or migrate-app skipping because "
        f"target paths already exist. If outputs are stale, remove them under src/main/java and re-run.",
    )


def run_verification(
    source_inventory: dict[str, Any] | None,
    spring_repo: Path,
) -> dict[str, Any]:
    inv = source_inventory or {}
    by_play = inv.get("by_layer") or {k: 0 for k in LAYER_ORDER}
    spring_total, by_spring = scan_spring_java(spring_repo)
    layer_comp: dict[str, Any] = {}
    worst = "passed"
    notes: list[str] = []
    for layer in LAYER_ORDER:
        exp = int(by_play.get(layer, 0) or 0)
        act = int(by_spring.get(layer, 0) or 0)
        delta = act - exp
        layer_comp[layer] = {"play_expected": exp, "spring_actual": act, "delta": delta}
        if exp > 0 and act < exp - 5:
            worst = "failed"
            notes.append(f"{layer}: spring count much lower than play ({act} vs {exp})")
        elif exp > 0 and act < exp:
            if worst == "passed":
                worst = "needs_review"
            notes.append(f"{layer}: fewer Spring files than Play ({act} vs {exp})")
    return {
        "status": worst,
        "checked_at": iso_now(),
        "spring_java_total": spring_total,
        "notes": "; ".join(notes) if notes else "Counts within tolerance or Play empty.",
        "layer_comparison": layer_comp,
    }
