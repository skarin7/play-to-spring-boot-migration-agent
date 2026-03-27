#!/usr/bin/env python3
"""
Autonomous Play → Spring migration orchestrator.
Single state file: migration-status.json (see play-to-spring-kit skills).

Only --play-repo is required (or PLAY_REPO). By default the script:

1. Builds ``java-dev-toolkit`` with Maven (sibling of ``play-to-spring-kit`` in the
   monorepo, or ``JAVA_DEV_TOOLKIT_ROOT`` / ``--toolkit-root``).
2. Copies ``dev-toolkit-*.jar`` into ``play-to-spring-kit/lib/``.
3. Runs ``setup.sh`` (idempotent: skills, JAR into the play repo, workspace.yaml,
   Spring dirs). Optional ``--export-play-conf`` flattens Play ``conf/application.conf``
   into Spring ``application.properties`` (needs ``pyhocon``). Then seeds
   ``migration-status.json`` if missing, then if
   ``initialize.status`` is not ``done`` and ``CURSOR_API_KEY`` is set, spawns
   ``cursor-agent`` with **builder skill Step 1 only** to bootstrap ``pom.xml`` / compile.
   After init, continues **migrate-app** scoped by **folder-based migration units**
   (``--path-prefix``) by default, or legacy **semantic layers** (``--layer``) when
   ``--use-semantic-layers`` / ``MIGRATION_USE_SEMANTIC_LAYERS=1``; then compile and
   optional cursor-agent for fixes.

Use ``--skip-build-toolkit`` if the JAR is already in ``lib/``.

Intended usage: from the monorepo, ``cd play-to-spring-kit`` and run
``python3 scripts/migration_orchestrator.py --play-repo ../your-play-app``.
Kit paths come from ``__file__`` (not cwd); ``--play-repo`` / ``--workspace`` are
resolved relative to the shell's current working directory.

**Cursor Agent (CLI) vs IDE chat:** the script runs ``cursor-agent -p`` headlessly. The IDE
loads Agent Skills from ``.cursor/skills/`` when you pick a skill; the CLI only sees text we
put in the prompt (we inline builder Step 1 for bootstrap). The CLI ``--workspace`` must
include **both** Play and Spring trees when they are siblings under the migration workspace
(default: parent of the Play repo)—otherwise the agent behaves unlike a multi-root or
parent-folder IDE window. Use ``--cursor-force`` or ``CURSOR_AGENT_FORCE=1`` if terminal
commands block on approval. Set ``CURSOR_AGENT_WORKSPACE`` to an absolute path to override
``--workspace`` for unusual layouts.

Exit codes: 0 OK, 1 unexpected error, 2 stuck compile (no cursor), 3 init not done,
            4 budget exhausted, 5 fail-fast failure or blocking infrastructure_error (JDK/Lombok).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Any

# ---------------------------------------------------------------------------
# Defaults (override via env / CLI)
# ---------------------------------------------------------------------------

DEFAULT_CURSOR_MODEL = "composer-2"
# Used for early compile-fix rounds only when --optimise-compile-fix / MIGRATION_OPTIMISE_COMPILE_FIX.
CURSOR_MODEL_FIX_WHEN_OPTIMISED = "cursor-small"

MVN_PACKAGE_MISSING_RE = re.compile(
    r"package\s+([\w.]+)\s+does\s+not\s+exist",
    re.IGNORECASE,
)

LOG = logging.getLogger("migration_orchestrator")


def configure_logging(verbose: bool = False) -> None:
    """
    Log to stderr. Call once after parsing CLI (or set MIGRATION_VERBOSE=1).
    INFO: milestones + streamed cursor-agent lines; DEBUG: extra diagnostics.
    """
    level = logging.DEBUG if verbose else logging.INFO
    LOG.setLevel(level)
    if LOG.handlers:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s [migration] %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    LOG.addHandler(handler)
    LOG.propagate = False


def _heartbeat_while_process_runs(proc: subprocess.Popen, label: str, interval_sec: float = 30.0) -> None:
    """Background: log periodically while cursor-agent is still running."""
    start = time.monotonic()
    while proc.poll() is None:
        time.sleep(interval_sec)
        if proc.poll() is not None:
            break
        elapsed = time.monotonic() - start
        LOG.info(
            "%s: still running (%.0f s elapsed, pid=%s) — agent output appears above as it arrives.",
            label,
            elapsed,
            proc.pid,
        )


def _drain_stream(pipe, buf: list[str], stream_label: str) -> None:
    """Read lines from cursor-agent stdout/stderr; log each line in real time."""
    try:
        assert pipe is not None
        for line in iter(pipe.readline, ""):
            buf.append(line)
            text = line.rstrip()
            if not text:
                continue
            if stream_label == "stderr":
                LOG.warning("[cursor-agent stderr] %s", text)
            else:
                LOG.info("[cursor-agent stdout] %s", text)
    finally:
        try:
            pipe.close()
        except OSError:
            pass


def abs_path(p: Path) -> Path:
    """Expand ~ and resolve to absolute (relative segments use process cwd)."""
    return p.expanduser().resolve(strict=False)


def scripts_dir() -> Path:
    """Directory containing this script and ``setup.sh``."""
    return Path(__file__).resolve().parent


def kit_root() -> Path:
    """play-to-spring-kit root (``lib/``, ``skills/``, ``config/``, …)."""
    return scripts_dir().parent


def default_dev_toolkit_root() -> Path:
    """``java-dev-toolkit`` next to ``play-to-spring-kit`` (monorepo layout)."""
    return kit_root().parent / "java-dev-toolkit"


def resolved_dev_toolkit_root(cli_path: Path | None) -> Path:
    if cli_path is not None:
        return abs_path(cli_path)
    env = os.environ.get("JAVA_DEV_TOOLKIT_ROOT")
    if env:
        return abs_path(Path(env))
    return default_dev_toolkit_root()


def find_packaged_toolkit_jar(toolkit_root: Path) -> Path | None:
    """Shaded ``dev-toolkit-*.jar`` under ``target/`` (exclude sources/javadoc)."""
    td = toolkit_root / "target"
    if not td.is_dir():
        return None
    jars = [p for p in td.glob("dev-toolkit-*.jar") if p.is_file()]
    if not jars:
        return None

    def sort_key(p: Path) -> tuple[int, str]:
        n = p.name.lower()
        if "sources" in n or "javadoc" in n:
            return (1, p.name)
        return (0, p.name)

    jars.sort(key=sort_key)
    return jars[0]


def ensure_jar_in_kit_lib(
    *,
    skip_build: bool,
    toolkit_root: Path,
    dry_run: bool,
) -> int:
    """
    Build ``java-dev-toolkit`` and copy JAR to ``play-to-spring-kit/lib/``,
    or verify ``lib/`` already contains a JAR when ``skip_build`` is True.

    Returns 0 on success, 1 on failure.
    """
    lib_dir = kit_root() / "lib"
    if skip_build:
        existing = sorted(lib_dir.glob("*.jar"))
        if not existing:
            print(
                "ERROR: --skip-build-toolkit was set but no JAR was found in "
                f"{lib_dir}. Build the toolkit and copy dev-toolkit-*.jar there, "
                "or omit --skip-build-toolkit to build automatically.",
                file=sys.stderr,
            )
            return 1
        print(
            f"[orchestrator] Using existing JAR in {lib_dir}: {existing[0].name}",
            flush=True,
        )
        return 0

    if not toolkit_root.is_dir():
        print(
            f"ERROR: java-dev-toolkit directory not found: {toolkit_root}\n"
            "  Clone the full repo (sibling layout: java-dev-toolkit + play-to-spring-kit), "
            "or set JAVA_DEV_TOOLKIT_ROOT / --toolkit-root, or use --skip-build-toolkit "
            f"if the JAR is already under {lib_dir}/.",
            file=sys.stderr,
        )
        return 1
    if not (toolkit_root / "pom.xml").is_file():
        print(
            f"ERROR: not a Maven project (missing pom.xml): {toolkit_root}",
            file=sys.stderr,
        )
        return 1

    if dry_run:
        print(
            f"[dry-run] mvn package -DskipTests in {toolkit_root}; "
            f"copy dev-toolkit-*.jar -> {lib_dir}/",
            file=sys.stderr,
        )
        return 0

    print(f"[orchestrator] Building dev-toolkit: mvn package -DskipTests in {toolkit_root}", flush=True)
    p = subprocess.run(
        ["mvn", "package", "-DskipTests"],
        cwd=str(toolkit_root),
    )
    if p.returncode != 0:
        print("ERROR: mvn package failed (dev-toolkit build).", file=sys.stderr)
        return 1

    jar = find_packaged_toolkit_jar(toolkit_root)
    if jar is None:
        print(
            f"ERROR: no dev-toolkit-*.jar under {toolkit_root / 'target'} after build.",
            file=sys.stderr,
        )
        return 1

    lib_dir.mkdir(parents=True, exist_ok=True)
    dest = lib_dir / jar.name
    shutil.copy2(jar, dest)
    print(f"[orchestrator] Installed {jar.name} -> {dest}", flush=True)
    return 0


def parse_workspace_yaml(yaml_path: Path) -> dict[str, str]:
    """Minimal key: value reader for kit-generated workspace.yaml (no PyYAML)."""
    out: dict[str, str] = {}
    if not yaml_path.is_file():
        return out
    for line in yaml_path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if ":" not in s:
            continue
        key, _, val = s.partition(":")
        key = key.strip()
        val = val.split("#", 1)[0].strip().strip('"').strip("'")
        if key in (
            "play_repo",
            "spring_repo",
            "migration_root",
            "batch_size",
            "base_package",
            "kit_path",
            "migration_unit_root",
        ):
            out[key] = val
    return out


def normalize_spring_repo_str(raw: str, workspace_dir: Path) -> Path:
    """
    Resolve spring_repo from workspace.yaml. Fixes POSIX expanduser('~//x') -> '//x'.
    """
    s = raw.strip().strip('"').strip("'")
    s = os.path.expanduser(s)
    if s.startswith("//") and not s.startswith("///") and len(s) > 2:
        rest = s[2:].lstrip("/")
        s = str(Path.home() / rest) if rest else str(Path.home())
    p = Path(s)
    if p.is_absolute():
        return p.resolve()
    return (workspace_dir / p).resolve()


def resolve_spring_repo(
    play_repo: Path,
    workspace_dir: Path,
    spring_name: str | None,
) -> Path:
    """
    Same layout as kit bootstrap: read workspace.yaml in workspace_dir, else
    <workspace_dir>/spring-<play_basename> or <workspace_dir>/<spring_name>.
    """
    ws_yaml = workspace_dir / "workspace.yaml"
    data = parse_workspace_yaml(ws_yaml)
    if data.get("spring_repo"):
        spr = normalize_spring_repo_str(data["spring_repo"], workspace_dir)
        declared = data.get("play_repo")
        if declared:
            try:
                decl_p = normalize_spring_repo_str(declared, workspace_dir)
                if decl_p != play_repo.resolve():
                    print(
                        "[warn] workspace.yaml play_repo does not match --play-repo; "
                        f"using spring_repo from yaml ({spr}).",
                        file=sys.stderr,
                    )
            except OSError:
                pass
        return spr
    if spring_name:
        return (workspace_dir / spring_name).resolve()
    return (workspace_dir / f"spring-{play_repo.name}").resolve()


def resolve_status_path(
    spring_repo: Path,
    cli_path: Path | None,
) -> Path:
    """
    Precedence: --status-file, then MIGRATION_STATUS_FILE, else
    <spring-repo>/migration-status.json
    """
    if cli_path is not None:
        return abs_path(cli_path)
    env_p = os.environ.get("MIGRATION_STATUS_FILE")
    if env_p:
        return abs_path(Path(env_p))
    base = spring_repo if spring_repo.is_absolute() else spring_repo.resolve()
    return (base / "migration-status.json").resolve(strict=False)


def cursor_agent_workspace_root(
    play_repo: Path,
    spring_repo: Path,
    workspace_dir: Path,
) -> Path:
    """
    Directory for ``cursor-agent --workspace``: should contain both Play and Spring when
    possible (default kit layout: both under ``workspace_dir``).
    """
    w = workspace_dir.resolve()
    pr = play_repo.resolve()
    sr = spring_repo.resolve()

    def is_under(root: Path, p: Path) -> bool:
        try:
            p.relative_to(root)
            return True
        except ValueError:
            return False

    if is_under(w, pr) and is_under(w, sr):
        return w
    parent = pr.parent
    if is_under(parent, pr) and is_under(parent, sr):
        return parent
    if is_under(pr, sr):
        return pr
    if is_under(sr, pr):
        return sr
    if is_under(w, pr):
        return w
    return pr


def resolve_cursor_agent_workspace(
    play_repo: Path,
    spring_repo: Path,
    workspace_dir: Path,
) -> Path:
    """``CURSOR_AGENT_WORKSPACE`` override, else shared root from ``cursor_agent_workspace_root``."""
    raw = os.environ.get("CURSOR_AGENT_WORKSPACE", "").strip()
    if raw:
        return abs_path(Path(raw))
    return cursor_agent_workspace_root(play_repo, spring_repo, workspace_dir)


def run_setup_sh(
    play_repo: Path,
    workspace_dir: Path,
    spring_name: str | None,
    dry_run: bool,
) -> int:
    """Run kit install/bootstrap (idempotent). Called automatically at orchestrator start."""
    setup_sh = scripts_dir() / "setup.sh"
    if not setup_sh.is_file():
        print(f"ERROR: kit install script missing at {setup_sh}", file=sys.stderr)
        return 1
    # Always pass --workspace so setup and this script use the same directory (default = parent of play).
    cmd = [
        "bash",
        str(setup_sh),
        str(play_repo),
        "--workspace",
        str(workspace_dir),
    ]
    if spring_name:
        cmd.extend(["--spring-name", spring_name])
    if dry_run:
        print("[dry-run]", " ".join(shlex.quote(a) for a in cmd), file=sys.stderr)
        return 0
    p = subprocess.run(
        cmd,
        cwd=str(kit_root()),
        capture_output=True,
        text=True,
    )
    if p.stdout:
        print(p.stdout, end="" if p.stdout.endswith("\n") else "\n")
    if p.stderr:
        print(p.stderr, end="" if p.stderr.endswith("\n") else "\n", file=sys.stderr)
    return p.returncode


def run_export_play_conf(
    play_repo: Path,
    spring_repo: Path,
    extra_strip_prefixes: list[str],
    dry_run: bool,
) -> None:
    """
    Optionally flatten Play conf/application.conf into Spring application.properties.

    Non-fatal: missing conf file, missing converter script, missing pyhocon, or converter
    errors only log warnings. Always strips ``akka.`` keys; ``extra_strip_prefixes`` add more.
    """
    conf_path = play_repo / "conf" / "application.conf"
    if not conf_path.is_file():
        LOG.info("export-play-conf: skip (no %s)", conf_path)
        return
    script = scripts_dir() / "conf_to_application_properties.py"
    if not script.is_file():
        LOG.warning("export-play-conf: converter missing at %s (skipped)", script)
        return
    out_path = spring_repo / "src" / "main" / "resources" / "application.properties"
    cmd: list[str] = [sys.executable, str(script), "-i", str(conf_path), "-o", str(out_path)]
    for raw in ["akka."] + list(extra_strip_prefixes or []):
        p = raw.strip()
        if p:
            cmd.extend(["--strip-prefix", p])
    if dry_run:
        LOG.info("[dry-run] %s", " ".join(shlex.quote(a) for a in cmd))
        return
    proc = subprocess.run(
        cmd,
        cwd=str(kit_root()),
        capture_output=True,
        text=True,
    )
    if proc.stdout:
        LOG.info("%s", proc.stdout.rstrip())
    if proc.stderr:
        LOG.info("%s", proc.stderr.rstrip())
    if proc.returncode == 2:
        req = scripts_dir() / "requirements-conf.txt"
        LOG.warning(
            "export-play-conf: pyhocon not installed (skipped). "
            "Use repo ./start_upgrade.sh (kit .venv) or: python3 -m pip install -r %s",
            req,
        )
        return
    if proc.returncode != 0:
        LOG.warning(
            "export-play-conf: converter exited with code %s (continuing)",
            proc.returncode,
        )


LAYER_ORDER = (
    "model",
    "repository",
    "manager",
    "service",
    "controller",
    "other",
)

# Slice statuses that must not be treated as a successful migration (avoid current_step=done).
_SLICE_TERMINAL_FAILURE_STATUSES: frozenset[str] = frozenset(
    (
        "loop_detected",
        "failed",
        "timeout",
        "budget_exhausted",
        "needs_manual_fix",
        "no_migrated_output",
    )
)

MIGRATE_DONE_RE = re.compile(
    r"migrate-app done:\s*(\d+)\s*files,\s*(\d+)\s*errors,\s*(\d+)\s*remaining",
    re.IGNORECASE,
)

# Maven: [ERROR] /path/File.java:[10,20] error: message
MVN_ERROR_RE = re.compile(
    r"\[ERROR\]\s+(.+\.java):\[(\d+),[^\]]+\]\s*(.+)",
)

COMPILATION_ERROR_LINE_RE = re.compile(r"^\[ERROR\]\s+COMPILATION ERROR", re.MULTILINE)


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def classify_layer(relative_path: str) -> str:
    """LayerDetector-aligned order: controller → service → model → manager → repository → other."""
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


CONTROLLER_DIR_NAMES = frozenset({"controllers", "controller"})


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
    """Descend through ≤2-wide chains until a directory has ≥3 subdirs."""
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
    See kit plan: controllers-folder anchor, ≥3-subdir fallback, optional workspace override.
    """
    app_dir = (play_root / java_subdir).resolve()
    if not app_dir.is_dir():
        return [
            {
                "id": "app_root",
                "path_prefix": "",
                "java_file_count": 0,
                "discovered_by": "empty_app",
            }
        ]

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
                out.append(
                    {
                        "id": uid,
                        "path_prefix": px,
                        "java_file_count": cnt,
                        "discovered_by": "migration_unit_root",
                    }
                )
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
                out.append(
                    {
                        "id": uid,
                        "path_prefix": px,
                        "java_file_count": cnt,
                        "discovered_by": "controllers_anchor",
                    }
                )
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
                out.append(
                    {
                        "id": uid,
                        "path_prefix": px,
                        "java_file_count": cnt,
                        "discovered_by": "three_plus_subdirs",
                    }
                )
            return out

    total = _count_java_files(app_dir)
    return [
        {
            "id": "app_root",
            "path_prefix": "",
            "java_file_count": total,
            "discovered_by": "whole_app",
        }
    ]


def default_unit_entry(
    unit_id: str,
    path_prefix: str,
    java_file_count: int,
) -> dict[str, Any]:
    base = default_layer_entry()
    base["id"] = unit_id
    base["path_prefix"] = path_prefix
    base["java_file_count"] = int(java_file_count)
    return base


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
            fresh["discovered_by"] = d.get("discovered_by")
            out.append(fresh)
    for uid, old in by_id.items():
        if uid not in seen_ids and old.get("status") == "done":
            out.append(old)
    return sorted(out, key=lambda u: (u.get("path_prefix") or ""))


def reset_transform_progress_after_bootstrap(status: dict[str, Any]) -> None:
    """
    After initialize completes in this run, clear stale ``done`` flags on slices/layers.

    Otherwise ``merge_discovered_migration_units`` keeps old ``status: done`` and the
    orchestrator skips all ``migrate-app`` work (looks like it stopped after bootstrap).
    """
    status["current_step"] = "transform_validate"
    status["failed_layers"] = []
    status["migration_verification"] = None
    auto = status.setdefault("autonomous", {})
    auto["total_llm_calls"] = 0
    auto["last_errors_path"] = None

    for u in status.get("migration_units") or []:
        if not isinstance(u, dict):
            continue
        u["status"] = "pending"
        u["retry_count"] = 0
        u["llm_calls"] = 0
        u["errors_history"] = []
        u["files_migrated"] = 0
        u["files_failed"] = []
        u["validate_iteration"] = 0
        u["last_error_count"] = None
        u["failure_reason"] = None
        u.pop("compile_error_counts", None)

    for lyr in LAYER_ORDER:
        le = status["layers"][lyr]
        le["status"] = "pending"
        le["retry_count"] = 0
        le["llm_calls"] = 0
        le["errors_history"] = []
        le["files_migrated"] = 0
        le["files_failed"] = []
        le["validate_iteration"] = 0
        le["last_error_count"] = None
        le["failure_reason"] = None
        le.pop("compile_error_counts", None)


def default_layer_entry() -> dict[str, Any]:
    return {
        "status": "pending",
        "retry_count": 0,
        "llm_calls": 0,
        "errors_history": [],
        "files_migrated": 0,
        "files_failed": [],
        "validate_iteration": 0,
        "last_error_count": None,
        "failure_reason": None,
    }


def default_autonomous() -> dict[str, Any]:
    return {
        "total_llm_calls": 0,
        "max_total_llm_calls": int(os.environ.get("MAX_TOTAL_LLM_CALLS", "50")),
        "cursor_model": DEFAULT_CURSOR_MODEL,
        "cursor_model_fix": DEFAULT_CURSOR_MODEL,
        "cursor_model_escalate": DEFAULT_CURSOR_MODEL,
        "escalate_after_retries": int(os.environ.get("ESCALATE_AFTER_RETRIES", "2")),
        "max_files_per_cursor_session": int(
            os.environ.get("MAX_FILES_PER_CURSOR_SESSION", "10")
        ),
        "last_errors_path": None,
    }


def merge_status(raw: dict[str, Any]) -> dict[str, Any]:
    """Ensure all expected keys exist (backward compatible)."""
    out = dict(raw)
    out.setdefault("current_step", "transform_validate")
    out.setdefault("initialize", {})
    init = out["initialize"]
    init.setdefault("status", "pending")
    init.setdefault("pom_generated", False)
    init.setdefault("application_java_generated", False)
    init.setdefault("application_properties_generated", False)
    init.setdefault("error", None)

    out.setdefault("layers", {})
    for layer in LAYER_ORDER:
        le = {**default_layer_entry(), **out["layers"].get(layer, {})}
        out["layers"][layer] = le

    out.setdefault("failed_layers", [])
    out.setdefault("autonomous", {})
    auto = {**default_autonomous(), **out["autonomous"]}
    # env can tighten max if JSON has higher? Use max of JSON and env default from first run — keep JSON value if set
    if "max_total_llm_calls" not in raw.get("autonomous", {}):
        auto["max_total_llm_calls"] = int(os.environ.get("MAX_TOTAL_LLM_CALLS", "50"))
    out["autonomous"] = auto

    out.setdefault("source_inventory", None)
    out.setdefault("migration_verification", None)

    raw_mu = out.get("migration_units")
    if raw_mu is None:
        out.setdefault("migration_units", None)
    elif isinstance(raw_mu, list):
        merged_list: list[dict[str, Any]] = []
        for u in raw_mu:
            if not isinstance(u, dict):
                continue
            uid = str(u.get("id") or "unit")
            px = str(u.get("path_prefix", ""))
            jc = int(u.get("java_file_count", 0))
            merged_list.append({**default_unit_entry(uid, px, jc), **u})
        out["migration_units"] = merged_list
    else:
        out["migration_units"] = None

    return out


def migrate_v1_to_v2(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Upgrade v1 migration-status.json to v2 schema in-place.

    Changes:
    - Sets ``schema_version: 2``
    - Converts ``errors_history`` (list of full error dicts) to ``error_fingerprints``
      (list of sorted ``"file:line:msg"`` string lists), truncated to last 5 entries
    - Adds ``det_fix_log: []`` to each unit/layer that lacks it
    - Adds ``prompt_cache_key: null`` to ``autonomous`` block if absent
    - Preserves ``status``, ``files_migrated``, ``validate_iteration`` unchanged
    """
    if raw.get("schema_version") == 2:
        return raw

    raw["schema_version"] = 2

    # Convert units
    for u in raw.get("migration_units") or []:
        if not isinstance(u, dict):
            continue
        _convert_errors_history_to_fingerprints(u)
        u.setdefault("det_fix_log", [])
        u.setdefault("llm_calls", 0)

    # Convert layers
    for _layer_name, le in (raw.get("layers") or {}).items():
        if not isinstance(le, dict):
            continue
        _convert_errors_history_to_fingerprints(le)
        le.setdefault("det_fix_log", [])

    # Autonomous block
    auto = raw.setdefault("autonomous", {})
    auto.setdefault("prompt_cache_key", None)

    return raw


def _convert_errors_history_to_fingerprints(entry: dict[str, Any]) -> None:
    """
    Convert ``errors_history`` (v1: list of full error dicts) to
    ``error_fingerprints`` (v2: list of sorted ``"file:line:msg"`` string lists),
    truncated to last 5 entries.
    """
    hist = entry.pop("errors_history", None)
    if entry.get("error_fingerprints") is not None:
        return  # already converted

    if hist is None:
        entry["error_fingerprints"] = []
        return

    fingerprints: list[list[str]] = []
    for round_errors in hist:
        if isinstance(round_errors, list):
            fps: list[str] = []
            for e in round_errors:
                if isinstance(e, dict):
                    fp = e.get("file", "")
                    line = e.get("line", 0)
                    msg = (e.get("message") or "").strip()
                    fps.append(f"{fp}:{line}:{msg}")
                elif isinstance(e, str):
                    fps.append(e)  # already a fingerprint string
            fingerprints.append(sorted(fps))
        elif isinstance(round_errors, str):
            fingerprints.append([round_errors])

    # Keep only last 5 rounds
    entry["error_fingerprints"] = fingerprints[-5:]


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def parse_migrate_output(text: str) -> tuple[int, int, int] | None:
    m = MIGRATE_DONE_RE.search(text)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def parse_mvn_errors(log: str) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for line in log.splitlines():
        m = MVN_ERROR_RE.search(line)
        if m:
            errors.append(
                {
                    "file": m.group(1),
                    "line": int(m.group(2)),
                    "message": m.group(3).strip(),
                }
            )
    return errors


def count_compilation_errors(log: str) -> int:
    return len(COMPILATION_ERROR_LINE_RE.findall(log))


def normalize_errors(errors: list[dict[str, Any]]) -> list[str]:
    sigs = []
    for e in errors:
        fp = e.get("file", "")
        line = e.get("line", 0)
        msg = (e.get("message") or "").strip()
        sigs.append(f"{fp}:{line}:{msg}")
    return sorted(sigs)


def error_signature(e: dict[str, Any]) -> str:
    fp = e.get("file", "")
    line = e.get("line", 0)
    msg = (e.get("message") or "").strip()
    return f"{fp}:{line}:{msg}"


def classify_compile_errors(
    errors: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Split Maven-style errors into infrastructure / missing-dependency / code buckets.

    Infrastructure: compiler plugin crashed (Lombok/JDK, etc.) — not fixable by editing .java alone.
    Missing dependency: ``package X does not exist`` for known third-party prefixes.
    Code: everything else (and unknown package missing).
    """
    infra: list[dict[str, Any]] = []
    dep: list[dict[str, Any]] = []
    code: list[dict[str, Any]] = []
    for e in errors:
        fp = str(e.get("file", "") or "")
        msg = (e.get("message") or "").strip()
        low = msg.lower()
        if (
            "fatal error compiling" in low
            or "exceptionininitializererror" in low
            or ("typetag" in low and "unknown" in low)
        ) and (fp == "unknown" or not fp.endswith(".java")):
            infra.append(e)
            continue
        m = MVN_PACKAGE_MISSING_RE.search(msg)
        if m:
            pkg = m.group(1).strip()
            if pkg.startswith(
                (
                    "javax.inject",
                    "javax.annotation",
                    "jakarta.annotation",
                    "org.neo4j.driver.v1",
                    "com.google.common",
                    "play.",
                )
            ):
                dep.append(e)
            else:
                code.append(e)
            continue
        code.append(e)
    return infra, dep, code


# package prefix -> (groupId, artifactId, version or "" to omit version — BOM manages it)
_KNOWN_DEP_BY_PACKAGE_PREFIX: list[tuple[str, str, str, str]] = [
    ("com.google.common", "com.google.guava", "guava", "33.3.1-jre"),
    ("javax.inject", "javax.inject", "javax.inject", "1"),
    ("javax.annotation", "jakarta.annotation", "jakarta.annotation-api", ""),
    ("org.neo4j.driver.v1", "org.neo4j.driver", "neo4j-java-driver", ""),
]


def _pom_declares_dependency(text: str, group_id: str, artifact_id: str) -> bool:
    """Heuristic: same artifactId appears near groupId in pom (handles multi-module)."""
    if f"<artifactId>{artifact_id}</artifactId>" not in text:
        return False
    i = 0
    while True:
        j = text.find(f"<artifactId>{artifact_id}</artifactId>", i)
        if j < 0:
            return False
        window = text[max(0, j - 400) : j + 80]
        if f"<groupId>{group_id}</groupId>" in window:
            return True
        i = j + 1


def _dependency_xml_block(group_id: str, artifact_id: str, version: str) -> str:
    ver_line = (
        f"\n            <version>{version}</version>"
        if version
        else ""
    )
    return f"""
        <dependency>
            <groupId>{group_id}</groupId>
            <artifactId>{artifact_id}</artifactId>{ver_line}
        </dependency>"""


def try_deterministic_pom_fix(
    spring_repo: Path,
    dep_errors: list[dict[str, Any]],
    dry_run: bool,
) -> int:
    """
    Add known Maven coordinates for missing-package errors. Returns count of deps added.
    """
    if not dep_errors or dry_run:
        return 0
    pom = spring_repo / "pom.xml"
    if not pom.is_file():
        return 0
    text = pom.read_text(encoding="utf-8", errors="replace")
    needed: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for e in dep_errors:
        m = MVN_PACKAGE_MISSING_RE.search((e.get("message") or ""))
        if not m:
            continue
        pkg = m.group(1).strip()
        for prefix, gid, aid, ver in _KNOWN_DEP_BY_PACKAGE_PREFIX:
            if pkg == prefix or pkg.startswith(prefix + "."):
                key = (gid, aid)
                if key not in seen:
                    seen.add(key)
                    needed.append((gid, aid, ver))
                break
    blocks: list[str] = []
    for gid, aid, ver in needed:
        if _pom_declares_dependency(text, gid, aid):
            continue
        blocks.append(_dependency_xml_block(gid, aid, ver))
        LOG.info(
            "try_deterministic_pom_fix: will add dependency %s:%s%s",
            gid,
            aid,
            f":{ver}" if ver else " (no explicit version)",
        )
    if not blocks:
        return 0
    close = "</dependencies>"
    idx = text.find(close)
    if idx < 0:
        LOG.warning("try_deterministic_pom_fix: no %s in pom.xml", close)
        return 0
    new_text = text[:idx] + "".join(blocks) + "\n    " + text[idx:]
    pom.write_text(new_text, encoding="utf-8")
    return len(blocks)


def is_looping(current: list[str], history: list[list[str]]) -> bool:
    if len(history) < 1:
        return False
    if current == history[-1]:
        return True
    if len(history) >= 2 and current == history[-2]:
        return True
    prev_n = len(history[-1])
    cur_n = len(current)
    if prev_n == 0:
        return False
    # Progress: strictly fewer errors than last round — not a loop spike.
    if cur_n < prev_n:
        return False
    spike_threshold = max(int(prev_n * 1.5 + 0.999), prev_n + 5)
    if cur_n > spike_threshold:
        return True
    return False


def resolve_model(args: argparse.Namespace, status: dict[str, Any]) -> str:
    if getattr(args, "cursor_model", None):
        return args.cursor_model
    for key in ("CURSOR_MODEL", "MIGRATION_CURSOR_MODEL"):
        v = os.environ.get(key)
        if v:
            return v.strip()
    m = status.get("autonomous", {}).get("cursor_model")
    if m:
        return str(m)
    return DEFAULT_CURSOR_MODEL


def optimise_compile_fix_enabled(args: argparse.Namespace) -> bool:
    if getattr(args, "optimise_compile_fix", False):
        return True
    return os.environ.get("MIGRATION_OPTIMISE_COMPILE_FIX", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def resolve_fix_model(args: argparse.Namespace, status: dict[str, Any]) -> str:
    if getattr(args, "cursor_model_fix", None):
        return args.cursor_model_fix
    v = os.environ.get("CURSOR_MODEL_FIX") or os.environ.get("MIGRATION_CURSOR_MODEL_FIX")
    if v:
        return v.strip()
    if optimise_compile_fix_enabled(args):
        return CURSOR_MODEL_FIX_WHEN_OPTIMISED
    # Default: same model as main (legacy behavior before two-tier optimisation).
    return resolve_model(args, status)


def resolve_escalate_model(args: argparse.Namespace, status: dict[str, Any]) -> str:
    """Premium model after cheap fix attempts; defaults to main cursor model."""
    if getattr(args, "cursor_model_escalate", None):
        return args.cursor_model_escalate
    v = os.environ.get("CURSOR_MODEL_ESCALATE") or os.environ.get(
        "MIGRATION_CURSOR_MODEL_ESCALATE"
    )
    if v:
        return v.strip()
    m = status.get("autonomous", {}).get("cursor_model_escalate")
    if m:
        return str(m)
    return resolve_model(args, status)


def resolve_cursor_force(args: argparse.Namespace) -> bool:
    """Match IDE-style command approval: ``cursor-agent --force`` for headless runs."""
    if getattr(args, "cursor_force", False):
        return True
    return os.environ.get("CURSOR_AGENT_FORCE", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


@dataclass
class Guardrails:
    max_retries_per_layer: int = 5
    max_total_llm_calls: int = 50
    max_errors_llm: int = 10
    max_files_per_fix: int = 3
    timeout_layer_mins: int = 30
    max_files_per_cursor_session: int = 10
    cursor_agent_timeout_sec: int = 1800
    fix_model: str = DEFAULT_CURSOR_MODEL
    escalate_model: str = DEFAULT_CURSOR_MODEL
    escalate_after_retries: int = 2


def load_guardrails(args: argparse.Namespace, status: dict[str, Any]) -> Guardrails:
    auto = status.get("autonomous", {})
    return Guardrails(
        max_retries_per_layer=int(
            os.environ.get("MAX_RETRIES_PER_LAYER", str(args.max_retries_per_layer))
        ),
        max_total_llm_calls=int(
            auto.get(
                "max_total_llm_calls",
                int(os.environ.get("MAX_TOTAL_LLM_CALLS", str(args.max_total_llm_calls))),
            )
        ),
        max_errors_llm=int(
            os.environ.get("MAX_ERRORS_TO_SEND_LLM", str(args.max_errors_llm))
        ),
        max_files_per_fix=int(os.environ.get("MAX_FILES_PER_FIX", str(args.max_files_fix))),
        timeout_layer_mins=int(
            os.environ.get("TIMEOUT_PER_LAYER_MINS", str(args.timeout_layer_mins))
        ),
        max_files_per_cursor_session=int(
            auto.get(
                "max_files_per_cursor_session",
                int(
                    os.environ.get(
                        "MAX_FILES_PER_CURSOR_SESSION",
                        str(args.max_files_cursor_session),
                    )
                ),
            )
        ),
        cursor_agent_timeout_sec=int(
            os.environ.get("CURSOR_AGENT_TIMEOUT_SEC", "1800")
        ),
        fix_model=resolve_fix_model(args, status),
        escalate_model=resolve_escalate_model(args, status),
        escalate_after_retries=int(
            auto.get(
                "escalate_after_retries",
                int(
                    os.environ.get(
                        "ESCALATE_AFTER_RETRIES",
                        str(getattr(args, "escalate_after_retries", 2)),
                    )
                ),
            )
        ),
    )


def use_semantic_layer_mode(args: argparse.Namespace) -> bool:
    """Legacy loop over six semantic layers (``--layer`` only). Default is path-based migration units."""
    if getattr(args, "use_semantic_layers", False):
        return True
    return os.environ.get("MIGRATION_USE_SEMANTIC_LAYERS", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def run_cmd(
    argv: list[str],
    cwd: Path | None,
    dry_run: bool,
) -> subprocess.CompletedProcess[str]:
    if dry_run:
        print("[dry-run]", " ".join(argv), file=sys.stderr)
        return subprocess.CompletedProcess(argv, 0, "", "")
    return subprocess.run(
        argv,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=None,
    )


def run_mvn_compile(spring_repo: Path, dry_run: bool) -> tuple[int, str]:
    if dry_run:
        print("[dry-run] mvn -q compile", file=sys.stderr)
        return 0, ""
    p = subprocess.run(
        ["mvn", "-q", "compile"],
        cwd=str(spring_repo),
        capture_output=True,
        text=True,
        timeout=3600,
    )
    out = (p.stdout or "") + (p.stderr or "")
    return p.returncode, out


def group_errors_by_file(errors: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    g: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in errors:
        # normalize path key: take basename or full path as in log
        key = e.get("file", "unknown")
        g[key].append(e)
    return dict(g)


def build_error_batches(
    errors: list[dict[str, Any]],
    max_files: int,
) -> list[list[dict[str, Any]]]:
    """Batch errors by file; up to max_files distinct files per batch."""
    by_file = group_errors_by_file(errors)
    files = list(by_file.keys())
    batches: list[list[dict[str, Any]]] = []
    for i in range(0, len(files), max_files):
        chunk_files = files[i : i + max_files]
        batch: list[dict[str, Any]] = []
        for fp in chunk_files:
            batch.extend(by_file[fp])
        batches.append(batch)
    if not batches and errors:
        batches.append(errors[:])
    return batches


def call_cursor_agent(
    model: str,
    prompt: str,
    api_key: str,
    timeout_sec: int,
    dry_run: bool,
    cwd: Path | None = None,
    *,
    workspace: Path | None = None,
    force: bool = False,
    stream_output: bool = True,
    run_label: str = "cursor-agent",
) -> subprocess.CompletedProcess[str]:
    # Flags per https://cursor.com/docs/cli/reference/parameters
    argv: list[str] = [
        "cursor-agent",
        "-p",
        # Non-interactive runs must trust the workspace (otherwise CLI exits with a prompt).
        "--trust",
        "--output-format",
        "json",
        "--api-key",
        api_key,
        "--model",
        model,
    ]
    if force:
        argv.append("--force")
    ws = workspace if workspace is not None else cwd
    if ws is not None:
        argv.extend(["--workspace", str(ws.resolve())])
    argv.append(prompt)

    if dry_run:
        LOG.info("[dry-run] would run cursor-agent (%s, prompt=%d chars)", run_label, len(prompt))
        return subprocess.CompletedProcess(argv, 0, "", "")

    which = shutil.which("cursor-agent")
    if which:
        LOG.info("cursor-agent on PATH: %s", which)
    else:
        LOG.error(
            "cursor-agent not found on PATH — install the Cursor CLI / agent so `cursor-agent` is available."
        )

    preview = prompt[:240] + "…" if len(prompt) > 240 else prompt
    proc_cwd = str((cwd or ws).resolve()) if (cwd or ws) is not None else None
    LOG.info(
        "starting %s: model=%s timeout=%ds cursor_workspace=%s proc_cwd=%s force=%s prompt_chars=%d preview=%r",
        run_label,
        model,
        timeout_sec,
        ws,
        proc_cwd,
        force,
        len(prompt),
        preview,
    )
    LOG.debug("full argv (api key redacted): %s", _argv_repr_redacted(argv))

    if not stream_output:
        LOG.info("%s: streaming disabled; capturing output until process exits.", run_label)
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            cwd=proc_cwd,
        )
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        cwd=proc_cwd,
    )
    out_buf: list[str] = []
    err_buf: list[str] = []
    t_out = threading.Thread(
        target=_drain_stream,
        args=(proc.stdout, out_buf, "stdout"),
        daemon=True,
    )
    t_err = threading.Thread(
        target=_drain_stream,
        args=(proc.stderr, err_buf, "stderr"),
        daemon=True,
    )
    t_out.start()
    t_err.start()
    hb = threading.Thread(
        target=_heartbeat_while_process_runs,
        args=(proc, run_label),
        daemon=True,
    )
    hb.start()

    ret: int | None
    try:
        ret = proc.wait(timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        LOG.error(
            "%s: TIMEOUT after %ds — sending kill to pid %s",
            run_label,
            timeout_sec,
            proc.pid,
        )
        proc.kill()
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            LOG.error("%s: process did not exit after kill", run_label)
        ret = proc.returncode if proc.returncode is not None else -1

    t_out.join(timeout=30)
    t_err.join(timeout=30)

    stdout = "".join(out_buf)
    stderr = "".join(err_buf)
    LOG.info(
        "%s: finished exit_code=%s captured_stdout_chars=%d stderr_chars=%d",
        run_label,
        ret,
        len(stdout),
        len(stderr),
    )
    if ret != 0:
        LOG.warning(
            "%s: non-zero exit — if output was empty, run with -v / MIGRATION_VERBOSE=1 for DEBUG.",
            run_label,
        )
    return subprocess.CompletedProcess(argv, int(ret if ret is not None else 1), stdout, stderr)


def _argv_repr_redacted(argv: list[str]) -> str:
    out: list[str] = []
    skip_next = False
    for i, a in enumerate(argv):
        if skip_next:
            out.append("***")
            skip_next = False
            continue
        if a == "--api-key" and i + 1 < len(argv):
            out.append("--api-key")
            skip_next = True
            continue
        out.append(a)
    return " ".join(shlex.quote(x) for x in out)


_BUILDER_STEP1_HEADING = "## Step 1: Initialize Spring project"
_BUILDER_STEP2_HEADING = "\n## Step 2:"


def extract_builder_skill_step1_only(skill_markdown: str) -> str | None:
    """Return only the Initialize section from play-spring-builder SKILL.md."""
    i = skill_markdown.find(_BUILDER_STEP1_HEADING)
    if i == -1:
        return None
    j = skill_markdown.find(_BUILDER_STEP2_HEADING, i + len(_BUILDER_STEP1_HEADING))
    if j == -1:
        return skill_markdown[i:].strip()
    return skill_markdown[i:j].strip()


def load_builder_skill_step1_markdown(play_repo: Path) -> str:
    """
    Load builder skill from the play repo or kit, but only Step 1 (init).

    Bootstrapping used to inline the full orchestrator skill, which describes
    migrate-app and per-layer loops; agents followed that and never finished init.
    """
    candidates = [
        play_repo / ".cursor/skills/play-spring-builder/SKILL.md",
        kit_root() / "skills/builder-skill.md",
    ]
    for p in candidates:
        if not p.is_file():
            continue
        full = p.read_text(encoding="utf-8", errors="replace")
        chunk = extract_builder_skill_step1_only(full)
        if chunk:
            return chunk
        LOG.warning("Builder skill at %s missing %r section; using full file.", p, _BUILDER_STEP1_HEADING)
        return full.strip()
    return (
        f"# Missing builder skill. Run setup.sh.\n"
        f"# Expected: {candidates[0]} or {candidates[1]}\n"
    )


def bootstrap_initialize_via_cursor_agent(
    *,
    play_repo: Path,
    spring_repo: Path,
    cursor_workspace: Path,
    cursor_force: bool,
    status_path: Path,
    model: str,
    api_key: str,
    timeout_sec: int,
    dry_run: bool,
    max_attempts: int = 2,
) -> bool:
    """
    Spawn cursor-agent for Spring init only (builder Step 1), not full orchestration.

    Returns True if afterward initialize.status == done in migration-status.json.
    """
    skill_md = load_builder_skill_step1_markdown(play_repo)
    prompt = f"""You are invoked by the Play→Spring **migration_orchestrator.py** script (headless, non-interactive).

## Task — bootstrap / init ONLY (then STOP)

Complete **Step 1: Initialize Spring project** and **finish**. Do not run a full migration in this session.

1. Ensure `{status_path}` exists and keeps the skill schema (`initialize`, `layers`, optional `migration_units`, etc.).
2. Under **`{spring_repo}`**, create **`pom.xml`**, **`Application.java`**, and **`application.properties`** from the Play project's **`build.sbt`** and **`conf/application.conf`** (Play is **read-only**). Use **`base_package`** from **`workspace.yaml`** where the skill says to.
3. Run **`mvn -q compile`** with `{spring_repo}` as the working directory; fix **Spring-only** issues until compile succeeds (an empty Spring app besides `Application.java` should build).
4. Set in `{status_path}`: **`initialize.status` = `done`**, and `pom_generated` / `application_java_generated` / `application_properties_generated` = true. Keep **`current_step`** = **`initialize`** (the Python script advances it later).
5. **Do not** fill **`source_inventory`** here — the orchestrator script runs `scan_play_java` next unless disabled.

## FORBIDDEN in this run (will waste time or hang the pipeline)

- **`java -jar`** … **`migrate-app`** (including **`--layer`** or **`--path-prefix`**), or any dev-toolkit transform CLI
- Orchestrator **Step 2 / Step 3** (per-slice / per-layer transform, verification pass)
- Reading or following **play-spring-orchestrator** for full migration loops (not loaded in this prompt on purpose)

## Absolute paths
- **Cursor CLI workspace root** (contains both projects — same idea as opening this folder in the IDE): `{cursor_workspace}`
- Play repo: `{play_repo}`
- Spring repo: `{spring_repo}`
- State file: `{status_path}`

## Reference: play-spring-builder — Step 1 only
{skill_md}
"""
    for attempt in range(1, max_attempts + 1):
        label = f"spring-bootstrap attempt {attempt}/{max_attempts}"
        LOG.info(
            "Invoking cursor-agent for %s (timeout %ds). "
            "Live lines are prefixed with [cursor-agent stdout] / [cursor-agent stderr]; "
            "heartbeat every 30s if still running.",
            label,
            timeout_sec,
        )
        proc = call_cursor_agent(
            model,
            prompt,
            api_key,
            timeout_sec,
            dry_run,
            workspace=cursor_workspace,
            cwd=cursor_workspace,
            force=cursor_force,
            run_label=label,
        )
        if proc.returncode != 0:
            LOG.warning("cursor-agent exit code %s for %s", proc.returncode, label)
        if status_path.is_file():
            try:
                raw = json.loads(status_path.read_text(encoding="utf-8"))
                st = merge_status(raw)
                init_st = st["initialize"].get("status")
                LOG.info(
                    "After %s: read %s — initialize.status=%r",
                    label,
                    status_path,
                    init_st,
                )
                if init_st == "done":
                    LOG.info("Spring bootstrap OK: initialize.status is done.")
                    return True
            except (json.JSONDecodeError, OSError) as e:
                LOG.warning("Could not read/parse status after cursor-agent: %s", e)
        else:
            LOG.warning("Status file still missing after %s: %s", label, status_path)
    LOG.error("Spring bootstrap failed after %s attempts.", max_attempts)
    return False


def run_migrate_slice(
    play_repo: Path,
    jar: Path,
    spring_repo: Path,
    batch_size: int | None,
    dry_run: bool,
    *,
    layer: str | None = None,
    path_prefix: str | None = None,
) -> tuple[str, int, int, int]:
    """Invoke ``migrate-app`` with optional semantic ``--layer`` and/or ``--path-prefix`` (app-relative)."""
    argv = [
        "java",
        "-jar",
        str(jar),
        "migrate-app",
        "--target",
        str(spring_repo),
    ]
    if layer:
        argv.extend(["--layer", layer])
    if path_prefix is not None:
        px = normalize_path_prefix(str(path_prefix))
        if px:
            argv.extend(["--path-prefix", px])
    if batch_size:
        argv.extend(["--batch-size", str(batch_size)])
    proc = run_cmd(argv, play_repo, dry_run)
    out = (proc.stdout or "") + (proc.stderr or "")
    parsed = parse_migrate_output(out)
    if parsed is None:
        return out, 0, 0, -1
    n, m, r = parsed
    return out, n, m, r


def expected_play_java_for_slice(
    semantic_mode: bool,
    label: str,
    le: dict[str, Any],
    status: dict[str, Any],
) -> int:
    """How many Java files Play should contribute for this slice (from inventory or unit metadata)."""
    if semantic_mode:
        return int(
            (status.get("source_inventory") or {})
            .get("by_layer", {})
            .get(label, 0)
            or 0
        )
    return int(le.get("java_file_count", 0) or 0)


def migration_output_plausible(
    *,
    dry_run: bool,
    semantic_mode: bool,
    label: str,
    le: dict[str, Any],
    status: dict[str, Any],
    spring_repo: Path,
    n_add: int,
) -> tuple[bool, str]:
    """
    True if we should allow marking the slice done when ``mvn compile`` succeeds.

    Otherwise an empty Spring scaffold (e.g. only ``Application.java``) compiles while
    ``migrate-app`` wrote 0 files — a common false ``done``.
    """
    if dry_run:
        return True, ""
    exp = expected_play_java_for_slice(semantic_mode, label, le, status)
    if exp <= 0:
        return True, ""
    cumulative = int(le.get("files_migrated", 0) or 0)
    if n_add > 0 or cumulative > 0:
        return True, ""
    total_sp, by_sp = scan_spring_java(spring_repo)
    if semantic_mode:
        got = int(by_sp.get(label, 0) or 0)
        slack = max(2, min(6, exp // 4))
        if got >= max(1, exp - slack):
            return True, ""
        return (
            False,
            f"Play inventory expects ~{exp} Java files in layer {label!r}, but migrate-app "
            f"wrote 0 this round (files_migrated still 0) and Spring has {got} main/java files "
            f"in that layer — compile succeeded on an empty/partial tree. "
            f"dev-toolkit skips outputs that already exist; check wrong --layer, or delete stale "
            f"Spring sources to force re-migration.",
        )
    inv = status.get("source_inventory") or {}
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


def migrate_until_done(
    play_repo: Path,
    jar: Path,
    spring_repo: Path,
    batch_size: int | None,
    dry_run: bool,
    *,
    layer: str | None = None,
    path_prefix: str | None = None,
) -> tuple[int, int]:
    """Returns (total_files_processed, total_errors)."""
    total_n = 0
    total_m = 0
    prev_r = None
    while True:
        _, n, m, r = run_migrate_slice(
            play_repo,
            jar,
            spring_repo,
            batch_size,
            dry_run,
            layer=layer,
            path_prefix=path_prefix,
        )
        total_n += n
        total_m += m
        if dry_run:
            break
        if r < 0:
            break
        if r == 0:
            break
        if prev_r is not None and r >= prev_r:
            print(
                f"[warn] migrate-app remaining did not decrease ({prev_r} -> {r}), stopping.",
                file=sys.stderr,
            )
            break
        prev_r = r
    return total_n, total_m


def run_verification(
    status: dict[str, Any],
    play_repo: Path,
    spring_repo: Path,
) -> dict[str, Any]:
    inv = status.get("source_inventory") or {}
    by_play = inv.get("by_layer") or {k: 0 for k in LAYER_ORDER}
    spring_total, by_spring = scan_spring_java(spring_repo)
    layer_comp: dict[str, Any] = {}
    worst = "passed"
    notes: list[str] = []
    for layer in LAYER_ORDER:
        exp = int(by_play.get(layer, 0) or 0)
        act = int(by_spring.get(layer, 0) or 0)
        delta = act - exp
        layer_comp[layer] = {
            "play_expected": exp,
            "spring_actual": act,
            "delta": delta,
        }
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


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Play → Spring migration orchestrator (migration-status.json only). "
            "Builds java-dev-toolkit, copies JAR to lib/, runs setup.sh, then migrates. "
            "Only --play-repo is required."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Typical run from the monorepo (relative paths use your shell cwd):\n"
            "  cd path/to/play-to-spring-kit\n"
            "  python3 scripts/migration_orchestrator.py --play-repo ../my-play-app\n"
            "\n"
            "This builds ../java-dev-toolkit unless --skip-build-toolkit. "
            "Kit paths are resolved from this script's location, not from cwd."
        ),
    )
    pr = os.environ.get("PLAY_REPO")
    sr = os.environ.get("SPRING_REPO")
    parser.add_argument(
        "--play-repo",
        type=Path,
        default=Path(pr) if pr else None,
        help="Play project root (required unless PLAY_REPO). Kit bootstrap runs for this path first.",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="Migration workspace (default: parent of play repo). Resolved relative to cwd if relative.",
    )
    parser.add_argument(
        "--spring-name",
        default=None,
        help="Spring directory name under workspace if no workspace.yaml (default: spring-<play-basename>).",
    )
    parser.add_argument(
        "--spring-repo",
        type=Path,
        default=Path(sr) if sr else None,
        help="Spring project root (optional: derived from workspace.yaml or spring-<basename>).",
    )
    parser.add_argument(
        "--jar",
        type=Path,
        default=None,
        help="dev-toolkit JAR (default: <play-repo>/dev-toolkit-1.0.0.jar).",
    )
    parser.add_argument(
        "--status-file",
        type=Path,
        default=None,
        help="State file (default: <spring-repo>/migration-status.json). Overrides MIGRATION_STATUS_FILE.",
    )
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument(
        "--cursor-model",
        default=os.environ.get("CURSOR_MODEL")
        or os.environ.get("MIGRATION_CURSOR_MODEL")
        or None,
        help=f"cursor-agent --model (default {DEFAULT_CURSOR_MODEL}; see Cursor CLI docs).",
    )
    parser.add_argument(
        "--optimise-compile-fix",
        "-O",
        action="store_true",
        help=(
            f"Two-tier compile-fix: first rounds use {CURSOR_MODEL_FIX_WHEN_OPTIMISED}, "
            f"then escalate to --cursor-model (default {DEFAULT_CURSOR_MODEL}). "
            "Without this flag, all compile-fix rounds use the main model only. "
            "Or set MIGRATION_OPTIMISE_COMPILE_FIX=1."
        ),
    )
    parser.add_argument(
        "--cursor-model-fix",
        default=os.environ.get("CURSOR_MODEL_FIX")
        or os.environ.get("MIGRATION_CURSOR_MODEL_FIX")
        or None,
        help=(
            "Override compile-fix \"cheap\" model (implies you want a non-default first-pass model; "
            f"default without --optimise-compile-fix is same as --cursor-model). "
            "Env: CURSOR_MODEL_FIX."
        ),
    )
    parser.add_argument(
        "--cursor-model-escalate",
        default=os.environ.get("CURSOR_MODEL_ESCALATE")
        or os.environ.get("MIGRATION_CURSOR_MODEL_ESCALATE")
        or None,
        help=(
            "cursor-agent model after escalate-after-retries failed rounds "
            "(default: same as --cursor-model)."
        ),
    )
    parser.add_argument(
        "--escalate-after-retries",
        type=int,
        default=int(os.environ.get("ESCALATE_AFTER_RETRIES", "2")),
        help="Switch to escalate model after this many unsuccessful fix rounds (default 2).",
    )
    parser.add_argument("--no-cursor", action="store_true")
    parser.add_argument(
        "--cursor-force",
        action="store_true",
        help=(
            "Pass cursor-agent --force (non-interactive command approval). "
            "Or set CURSOR_AGENT_FORCE=1."
        ),
    )
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="DEBUG logging on stderr (also MIGRATION_VERBOSE=1).",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--skip-build-toolkit",
        action="store_true",
        help=(
            "Do not run Maven; require an existing JAR in play-to-spring-kit/lib/. "
            "Use when you already built java-dev-toolkit."
        ),
    )
    parser.add_argument(
        "--toolkit-root",
        type=Path,
        default=None,
        help=(
            "Path to java-dev-toolkit Maven project (default: sibling of play-to-spring-kit "
            "or JAVA_DEV_TOOLKIT_ROOT)."
        ),
    )
    parser.add_argument("--skip-inventory", action="store_true")
    parser.add_argument("--refresh-inventory", action="store_true")
    parser.add_argument(
        "--reset-migration-progress",
        action="store_true",
        help=(
            "Reset every layer and migration_unit to pending, clear failed_layers and "
            "migration_verification, and zero autonomous LLM counters so the transform "
            "phase runs again without re-running Spring bootstrap. "
            "Use when slices show loop_detected or status looks stuck after bootstrap. "
            "Or set MIGRATION_RESET_PROGRESS=1."
        ),
    )
    parser.add_argument(
        "--use-semantic-layers",
        action="store_true",
        help=(
            "Legacy: migrate by semantic layer only (model→…→other) using migrate-app --layer. "
            "Default: discover folder-based migration units under app/ and use --path-prefix. "
            "Or set MIGRATION_USE_SEMANTIC_LAYERS=1."
        ),
    )
    parser.add_argument(
        "--export-play-conf",
        action="store_true",
        help=(
            "After setup, run scripts/conf_to_application_properties.py: Play conf/application.conf "
            "→ Spring src/main/resources/application.properties (requires pyhocon; non-fatal if missing). "
            "Or set MIGRATION_EXPORT_PLAY_CONF=1."
        ),
    )
    parser.add_argument(
        "--conf-strip-prefix",
        action="append",
        default=[],
        metavar="PREFIX",
        help=(
            "With --export-play-conf: extra HOCON key prefixes to omit (repeatable). "
            "akka. is always stripped."
        ),
    )
    parser.add_argument("--max-retries-per-layer", type=int, default=5)
    parser.add_argument("--max-total-llm-calls", type=int, default=50)
    parser.add_argument("--max-errors-llm", type=int, default=10)
    parser.add_argument("--max-files-fix", type=int, default=3)
    parser.add_argument("--timeout-layer-mins", type=int, default=30)
    parser.add_argument("--max-files-cursor-session", type=int, default=10)

    args = parser.parse_args()

    configure_logging(
        verbose=bool(args.verbose)
        or os.environ.get("MIGRATION_VERBOSE", "").lower() in ("1", "true", "yes")
    )
    LOG.debug("migration_orchestrator starting (verbose=%s)", args.verbose)

    if not args.play_repo:
        print("ERROR: --play-repo (or PLAY_REPO) required.", file=sys.stderr)
        return 1

    play_repo = abs_path(args.play_repo)
    workspace_dir = abs_path(args.workspace) if args.workspace else play_repo.parent

    toolkit_root = resolved_dev_toolkit_root(args.toolkit_root)
    rc = ensure_jar_in_kit_lib(
        skip_build=args.skip_build_toolkit,
        toolkit_root=toolkit_root,
        dry_run=args.dry_run,
    )
    if rc != 0:
        return 1

    rc = run_setup_sh(
        play_repo,
        workspace_dir,
        args.spring_name,
        args.dry_run,
    )
    if rc != 0:
        return 1

    if args.spring_repo:
        spring_repo = abs_path(args.spring_repo)
    elif os.environ.get("SPRING_REPO"):
        spring_repo = abs_path(Path(os.environ["SPRING_REPO"]))
    else:
        spring_repo = resolve_spring_repo(play_repo, workspace_dir, args.spring_name)

    spring_repo = spring_repo.resolve()
    export_conf = bool(args.export_play_conf) or os.environ.get(
        "MIGRATION_EXPORT_PLAY_CONF", ""
    ).lower() in ("1", "true", "yes")
    if export_conf:
        run_export_play_conf(
            play_repo,
            spring_repo,
            list(args.conf_strip_prefix or []),
            args.dry_run,
        )
    status_path = resolve_status_path(spring_repo, args.status_file)
    cursor_workspace = resolve_cursor_agent_workspace(play_repo, spring_repo, workspace_dir)
    cursor_force = resolve_cursor_force(args)
    LOG.info(
        "cursor-agent shared workspace=%s (override with CURSOR_AGENT_WORKSPACE)",
        cursor_workspace,
    )

    if not status_path.is_file():
        seed = merge_status({})
        seed["current_step"] = "initialize"
        atomic_write_json(status_path, seed)
        print(
            f"[orchestrator] Created default state file (initialize pending): {status_path}",
            flush=True,
        )

    raw = json.loads(status_path.read_text(encoding="utf-8"))
    status = merge_status(raw)
    status = migrate_v1_to_v2(status)  # v1 → v2 schema upgrade (no-op if already v2)

    reset_progress = bool(getattr(args, "reset_migration_progress", False)) or (
        os.environ.get("MIGRATION_RESET_PROGRESS", "").strip().lower()
        in ("1", "true", "yes")
    )
    if reset_progress:
        LOG.info(
            "Resetting slice/layer progress (--reset-migration-progress / MIGRATION_RESET_PROGRESS)."
        )
        reset_transform_progress_after_bootstrap(status)
        status = merge_status(status)
        atomic_write_json(status_path, status)

    use_cursor = not args.no_cursor and bool(os.environ.get("CURSOR_API_KEY"))
    api_key = os.environ.get("CURSOR_API_KEY", "")
    bootstrap_just_ran = False

    if status["initialize"].get("status") != "done":
        if use_cursor:
            model_init = resolve_model(args, status)
            gr_init = load_guardrails(args, status)
            ok = bootstrap_initialize_via_cursor_agent(
                play_repo=play_repo,
                spring_repo=spring_repo,
                cursor_workspace=cursor_workspace,
                cursor_force=cursor_force,
                status_path=status_path,
                model=model_init,
                api_key=api_key,
                timeout_sec=gr_init.cursor_agent_timeout_sec,
                dry_run=args.dry_run,
            )
            if not ok:
                print(
                    "ERROR: Spring project init did not finish (initialize.status still not done). "
                    "Check cursor-agent output, CURSOR_API_KEY, and `cursor-agent` on PATH. "
                    "You can still open Cursor IDE and run skill play-spring-orchestrator manually.",
                    file=sys.stderr,
                )
                return 3
            bootstrap_just_ran = not args.dry_run
            raw = json.loads(status_path.read_text(encoding="utf-8"))
            status = merge_status(raw)
        else:
            print(
                "Initialize is not done: `migration-status.json` has initialize.status != done.\n"
                "  Export CURSOR_API_KEY so this script can spawn `cursor-agent` for Spring init (builder Step 1), "
                "or open Cursor and run play-spring-orchestrator (see setup.sh next steps), then re-run.",
                file=sys.stderr,
            )
            return 3

    jar = abs_path(args.jar) if args.jar else (play_repo / "dev-toolkit-1.0.0.jar")
    if not jar.is_file():
        print(f"ERROR: JAR not found: {jar}", file=sys.stderr)
        return 1

    model = resolve_model(args, status)
    status["autonomous"]["cursor_model"] = model
    gr = load_guardrails(args, status)
    status["autonomous"]["max_total_llm_calls"] = gr.max_total_llm_calls
    status["autonomous"]["max_files_per_cursor_session"] = gr.max_files_per_cursor_session
    status["autonomous"]["cursor_model_fix"] = gr.fix_model
    status["autonomous"]["cursor_model_escalate"] = gr.escalate_model
    status["autonomous"]["escalate_after_retries"] = gr.escalate_after_retries

    mig_dir = spring_repo / ".migration"
    mig_dir.mkdir(parents=True, exist_ok=True)

    ws_data = parse_workspace_yaml(workspace_dir / "workspace.yaml")
    semantic_mode = use_semantic_layer_mode(args)

    # Source inventory + migration units (path-based slices under app/)
    if not args.skip_inventory:
        inv_changed = False
        if (
            args.refresh_inventory
            or not status.get("source_inventory")
            or not status["source_inventory"].get("captured_at")
        ):
            status["source_inventory"] = scan_play_java(play_repo)
            inv_changed = True
        if not semantic_mode:
            if (
                args.refresh_inventory
                or not status.get("migration_units")
                or len(status.get("migration_units") or []) == 0
            ):
                disc = discover_migration_units(
                    play_repo,
                    migration_unit_root=ws_data.get("migration_unit_root"),
                )
                status["migration_units"] = merge_discovered_migration_units(
                    status.get("migration_units"),
                    disc,
                )
                inv_changed = True
        if inv_changed:
            status = merge_status(status)
            atomic_write_json(status_path, status)

    if not semantic_mode and args.skip_inventory:
        if not status.get("migration_units") or len(status.get("migration_units") or []) == 0:
            disc = discover_migration_units(
                play_repo,
                migration_unit_root=ws_data.get("migration_unit_root"),
            )
            status["migration_units"] = merge_discovered_migration_units(
                status.get("migration_units"),
                disc,
            )
            status = merge_status(status)
            atomic_write_json(status_path, status)

    if bootstrap_just_ran:
        LOG.info(
            "Initialize finished this run — resetting migration slice/layer progress so "
            "migrate-app runs (previous migration-status.json had slices marked done)."
        )
        reset_transform_progress_after_bootstrap(status)
        status = merge_status(status)
        atomic_write_json(status_path, status)

    status["current_step"] = "transform_validate"
    entity_start_time: dict[str, float] = {}

    try:
        if semantic_mode:
            entity_iterable: list[tuple[str, dict[str, Any], str | None, str | None]] = [
                (lyr, status["layers"][lyr], lyr, None) for lyr in LAYER_ORDER
            ]
        else:
            entity_iterable = []
            for i, u in enumerate(status.get("migration_units") or []):
                if not isinstance(u, dict):
                    continue
                uid = str(u.get("id") or f"unit_{i}")
                px = u.get("path_prefix")
                path_px = "" if px is None else str(px)
                entity_iterable.append((uid, u, None, path_px))

        mode_label = "semantic_layers" if semantic_mode else "path_units"
        preview = [x[0] for x in entity_iterable[:16]]
        more = " …" if len(entity_iterable) > 16 else ""
        LOG.info(
            "Transform phase: mode=%s, slice_count=%d%s%s",
            mode_label,
            len(entity_iterable),
            (", slices=" + ", ".join(preview)) if preview else "",
            more,
        )
        if not entity_iterable:
            LOG.error(
                "No migration slices to run: path mode has empty migration_units after inventory "
                "(or semantic mode has no layers). Use --refresh-inventory, avoid --skip-inventory "
                "unless units exist, or check Play app/ layout / workspace.yaml migration_unit_root."
            )
            status["current_step"] = "transform_validate"
            atomic_write_json(status_path, merge_status(status))
            return 6

        # Signatures from prior slices that ended loop_detected / max_retries — omit from LLM prompts.
        excluded_error_signatures: set[str] = set()

        for label, le, layer_arg, path_prefix_arg in entity_iterable:
            if le.get("status") == "done":
                continue

            if label not in entity_start_time:
                entity_start_time[label] = time.monotonic()

            # Transform
            le["status"] = "in_progress"
            atomic_write_json(status_path, status)

            n_add, m_err = migrate_until_done(
                play_repo,
                jar,
                spring_repo,
                args.batch_size,
                args.dry_run,
                layer=layer_arg,
                path_prefix=path_prefix_arg,
            )
            le["files_migrated"] = int(le.get("files_migrated", 0)) + n_add
            if m_err:
                print(
                    f"[warn] migrate-app reported {m_err} errors for slice {label}",
                    file=sys.stderr,
                )

            # Compile / fix loop
            while True:
                elapsed_mins = (time.monotonic() - entity_start_time[label]) / 60.0
                if elapsed_mins > gr.timeout_layer_mins:
                    le["status"] = "timeout"
                    le["failure_reason"] = "timeout"
                    status["failed_layers"].append(
                        {
                            "layer": label,
                            "reason": "timeout",
                            "last_errors_summary": None,
                        }
                    )
                    atomic_write_json(status_path, status)
                    if args.fail_fast:
                        return 5
                    break

                le["status"] = "compiling"
                atomic_write_json(status_path, status)

                code, log = run_mvn_compile(spring_repo, args.dry_run)
                le["validate_iteration"] = int(le.get("validate_iteration", 0)) + 1
                err_count = count_compilation_errors(log) or len(parse_mvn_errors(log))
                le["last_error_count"] = err_count

                if code == 0:
                    plausible, mig_reason = migration_output_plausible(
                        dry_run=args.dry_run,
                        semantic_mode=semantic_mode,
                        label=label,
                        le=le,
                        status=status,
                        spring_repo=spring_repo,
                        n_add=n_add,
                    )
                    if not plausible:
                        le["status"] = "no_migrated_output"
                        le["failure_reason"] = "no_migrated_output"
                        LOG.error("Slice %r: %s", label, mig_reason)
                        status["failed_layers"].append(
                            {
                                "layer": label,
                                "reason": "no_migrated_output",
                                "last_errors_summary": [mig_reason[:500]],
                            }
                        )
                        atomic_write_json(status_path, status)
                        if args.fail_fast:
                            return 5
                        break
                    le["status"] = "done"
                    le["failure_reason"] = None
                    le["errors_history"] = []
                    le.pop("compile_error_counts", None)
                    atomic_write_json(status_path, status)
                    break

                errors = parse_mvn_errors(log)
                if not errors and log:
                    # fallback: one blob
                    errors = [{"file": "unknown", "line": 0, "message": log[-4000:]}]

                infra, dep, code_e = classify_compile_errors(errors)
                if infra and not dep and not code_e:
                    le["status"] = "needs_manual_fix"
                    le["failure_reason"] = "infrastructure_error"
                    LOG.error(
                        "Slice %r: compiler/toolchain failure (e.g. Lombok vs JDK). "
                        "Fix pom.xml (lombok.version / maven-compiler-plugin) or JDK — not fixable by editing app Java.",
                        label,
                    )
                    status["failed_layers"].append(
                        {
                            "layer": label,
                            "reason": "infrastructure_error",
                            "last_errors_summary": normalize_errors(infra)[:20],
                        }
                    )
                    atomic_write_json(status_path, status)
                    break
                if infra and (dep or code_e):
                    LOG.warning(
                        "Slice %r: mixed toolchain + source errors; infrastructure lines omitted from LLM prompt.",
                        label,
                    )

                pom_added = try_deterministic_pom_fix(spring_repo, dep, args.dry_run)
                if pom_added > 0:
                    atomic_write_json(status_path, status)
                    continue

                norm = normalize_errors(errors)
                hist: list[list[str]] = list(le.get("errors_history") or [])

                if is_looping(norm, hist):
                    le["status"] = "loop_detected"
                    le["failure_reason"] = "loop_detected"
                    for sig in norm:
                        excluded_error_signatures.add(sig)
                    status["failed_layers"].append(
                        {
                            "layer": label,
                            "reason": "loop_detected",
                            "last_errors_summary": norm[:20],
                            "errors_history_tail": hist[-2:] if len(hist) >= 2 else hist,
                        }
                    )
                    atomic_write_json(status_path, status)
                    if args.fail_fast:
                        return 5
                    break

                # Stuck without cursor: three consecutive compiles where error count never goes down
                if not use_cursor:
                    if "compile_error_counts" not in le:
                        le["compile_error_counts"] = []
                    le["compile_error_counts"] = (le["compile_error_counts"] + [err_count])[-5:]
                    cc = le["compile_error_counts"]
                    if len(cc) >= 3 and cc[-1] >= cc[-2] >= cc[-3]:
                        print(
                            "Stuck compile (no cursor): error count not decreasing for 3 runs.",
                            file=sys.stderr,
                        )
                        le["status"] = "failed"
                        le["failure_reason"] = "stuck_compile"
                        atomic_write_json(status_path, status)
                        return 2

                if int(status["autonomous"]["total_llm_calls"]) >= gr.max_total_llm_calls:
                    le["status"] = "budget_exhausted"
                    le["failure_reason"] = "budget_exhausted"
                    status["failed_layers"].append(
                        {"layer": label, "reason": "budget_exhausted"}
                    )
                    atomic_write_json(status_path, status)
                    return 4

                if int(le.get("retry_count", 0)) >= gr.max_retries_per_layer:
                    le["status"] = "failed"
                    le["failure_reason"] = "max_retries"
                    for sig in norm:
                        excluded_error_signatures.add(sig)
                    status["failed_layers"].append(
                        {
                            "layer": label,
                            "reason": "max_retries",
                            "last_errors_summary": norm[:20],
                        }
                    )
                    atomic_write_json(status_path, status)
                    if args.fail_fast:
                        return 5
                    break

                llm_errors: list[dict[str, Any]] = []
                for e in code_e + dep:
                    if error_signature(e) in excluded_error_signatures:
                        continue
                    llm_errors.append(e)
                if not llm_errors:
                    llm_errors = [e for e in errors if e not in infra]
                if not llm_errors:
                    llm_errors = errors[: gr.max_errors_llm]

                dep_hint = ""
                if dep:
                    dep_hint = (
                        "\nSome errors are missing third-party packages — prefer adding the correct "
                        "Maven dependency in pom.xml when the package is an external library "
                        "(e.g. Guava, Neo4j driver).\n"
                    )

                if use_cursor:
                    le["status"] = "fixing"
                    err_path = mig_dir / f"errors-{label}.json"
                    err_path.write_text(json.dumps(llm_errors, indent=2), encoding="utf-8")
                    status["autonomous"]["last_errors_path"] = str(err_path)
                    atomic_write_json(status_path, status)

                    batches = build_error_batches(
                        llm_errors, gr.max_files_per_cursor_session
                    )
                    if not batches:
                        batches = [llm_errors[: gr.max_errors_llm]]

                    fix_round = int(le.get("retry_count", 0))
                    attempt_model = (
                        gr.escalate_model
                        if fix_round >= gr.escalate_after_retries
                        else gr.fix_model
                    )

                    for batch in batches:
                        top = batch[: gr.max_errors_llm]
                        slice_hint = ""
                        if path_prefix_arg is not None:
                            slice_hint = (
                                f'\nPlay ``app/`` path prefix for this slice: '
                                f'"{path_prefix_arg or "(whole app)"}"'
                            )
                        prompt = f"""Fix Spring Boot compilation errors for migration slice "{label}".{slice_hint}
Errors (JSON, top {len(top)}):
{json.dumps(top, indent=2)}
{dep_hint}
Rules:
- Fix one error category at a time where possible.
- Do not change public method signatures unless required to compile.
- Touch at most {gr.max_files_per_fix} files in this attempt.
- Only edit files under the Spring project; preserve business logic.
- Cursor workspace root (Play + Spring visible): {cursor_workspace}
- Spring repo root: {spring_repo}
- Play repo (read originals if needed): {play_repo}
"""
                        proc = call_cursor_agent(
                            attempt_model,
                            prompt,
                            api_key,
                            gr.cursor_agent_timeout_sec,
                            args.dry_run,
                            workspace=cursor_workspace,
                            cwd=cursor_workspace,
                            force=cursor_force,
                            run_label=(
                                f"compile-fix slice={label} model={attempt_model}"
                            ),
                        )
                        if proc.returncode != 0:
                            LOG.warning(
                                "compile-fix cursor-agent failed rc=%s (see streamed lines above)",
                                proc.returncode,
                            )
                        le["llm_calls"] = int(le.get("llm_calls", 0)) + 1
                        status["autonomous"]["total_llm_calls"] = int(
                            status["autonomous"]["total_llm_calls"]
                        ) + 1

                    le["retry_count"] = int(le.get("retry_count", 0)) + 1
                    hist.append(norm)
                    le["errors_history"] = hist[-20:]
                    atomic_write_json(status_path, status)
                else:
                    # No cursor: no code changes; stuck detection uses compile_error_counts only
                    le["retry_count"] = int(le.get("retry_count", 0)) + 1
                    hist.append(norm)
                    le["errors_history"] = hist[-20:]
                    atomic_write_json(status_path, status)
                # loop: compile again

            if le.get("failure_reason") == "infrastructure_error":
                LOG.error(
                    "Halting migration: slice %r has infrastructure_error (fix JDK / Lombok / compiler plugin).",
                    label,
                )
                atomic_write_json(status_path, status)
                return 5

        # Final compile check
        code, _ = run_mvn_compile(spring_repo, args.dry_run)
        if code != 0:
            print(
                "WARNING: final mvn compile failed after all migration slices.",
                file=sys.stderr,
            )

        status["current_step"] = "verify"
        status["migration_verification"] = run_verification(status, play_repo, spring_repo)

        slice_failures: list[str] = []
        if semantic_mode:
            for lyr in LAYER_ORDER:
                st = str(status["layers"][lyr].get("status") or "")
                if st in _SLICE_TERMINAL_FAILURE_STATUSES:
                    slice_failures.append(f"{lyr}={st}")
        else:
            for i, u in enumerate(status.get("migration_units") or []):
                if not isinstance(u, dict):
                    continue
                st = str(u.get("status") or "")
                if st in _SLICE_TERMINAL_FAILURE_STATUSES:
                    slice_failures.append(f"{u.get('id')}={st}")
            stale_lyr = [
                f"{lyr}={status['layers'][lyr].get('status')}"
                for lyr in LAYER_ORDER
                if str(status["layers"][lyr].get("status") or "")
                in _SLICE_TERMINAL_FAILURE_STATUSES
            ]
            if stale_lyr:
                LOG.warning(
                    "Path-based mode only uses migration_units; layers.* still show prior "
                    "semantic-run state (%s). Ignore or run with --reset-migration-progress to clear.",
                    "; ".join(stale_lyr[:12]) + (" …" if len(stale_lyr) > 12 else ""),
                )

        if slice_failures:
            status["current_step"] = "needs_attention"
            LOG.error(
                "One or more slices ended in a failure state: %s. "
                "current_step set to needs_attention (not done). "
                "Fix JDK/Lombok/pom or sources, then re-run; use --reset-migration-progress to "
                "clear pending/loop state after fixes.",
                "; ".join(slice_failures[:40])
                + (" …" if len(slice_failures) > 40 else ""),
            )
            atomic_write_json(status_path, merge_status(status))
            return 5

        status["current_step"] = "done"
        atomic_write_json(status_path, merge_status(status))

    except subprocess.TimeoutExpired:
        print("ERROR: subprocess timed out.", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
