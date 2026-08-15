"""Play-repo integrity guard (M6, Task 2): detects whether the read-only Play
repo was modified during a run.

The invariant -- LLM agents never write into the Play repo -- is already
enforced at write time by ``tools/fs.py``'s ``FsJail``. What is NOT enforced
anywhere: ``setup_ops.run_setup_sh`` runs ``bash setup.sh <play_repo>``, and
``toolkit_jar`` runs ``migrate-app``/``signature`` with ``cwd=play_repo``.
Neither is jailed, and nothing ever verifies the Play repo came out the other
side unchanged. This module is the detection layer for that gap -- lifted
from the ``play-to-springboot`` plugin's ``guard.py``, including the bug fix
that made it worth lifting.

Two modes:
  - git mode, only when the Play repo IS its own git repository root
    (``git rev-parse --show-toplevel`` resolves to the play_repo path).
  - manifest mode otherwise: a sha256-per-file manifest captured once, at
    bootstrap, and compared against on every check. Not a fallback for
    convenience -- it is strictly better in two ways a git-status check
    can't match: it catches gitignored files, and it never creates a nested
    git repo inside someone's checkout.

The bug this fixes: a Play repo that is not a git repository makes
``git status --porcelain`` exit 128 with EMPTY stdout -- which, read naively,
looks identical to "clean" (also empty stdout). There must be no code path
where an empty result means clean by default. This module always resolves
git-repo-ness explicitly first, and a missing baseline is ``"error"``, never
``"clean"`` -- a guard that cannot run is not a guard that passed.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Callable, Literal

GuardStatus = Literal["clean", "tampered", "error"]

RunCmd = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


def _default_run_cmd(argv: list[str]) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(argv, capture_output=True, text=True, timeout=30)


def _is_git_repo_root(play_repo: Path, *, run_cmd: RunCmd) -> bool:
    try:
        result = run_cmd(["git", "-C", str(play_repo), "rev-parse", "--show-toplevel"])
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    top = result.stdout.strip()
    if not top:
        return False
    try:
        return Path(top).resolve() == play_repo.resolve()
    except OSError:
        return False


def _git_status_clean(play_repo: Path, *, run_cmd: RunCmd) -> GuardStatus:
    try:
        result = run_cmd(["git", "-C", str(play_repo), "status", "--porcelain"])
    except (OSError, subprocess.TimeoutExpired):
        return "error"
    if result.returncode != 0:
        # Non-zero exit with a real git repo means the check itself failed
        # (corrupt repo, permissions) -- never read as clean.
        return "error"
    return "clean" if not result.stdout.strip() else "tampered"


def _iter_files(root: Path):
    for p in sorted(root.rglob("*")):
        if p.is_file():
            yield p


def build_manifest(play_repo: Path) -> dict[str, str]:
    """path (relative to play_repo) -> sha256 hex digest, for every file under
    play_repo. Includes gitignored files -- deliberately, since a non-git
    repo has no gitignore semantics to defer to, and a git repo that reaches
    manifest mode never happens (git mode is chosen first when applicable)."""
    manifest: dict[str, str] = {}
    for f in _iter_files(play_repo):
        digest = hashlib.sha256()
        try:
            with f.open("rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 16), b""):
                    digest.update(chunk)
        except OSError:
            continue
        manifest[str(f.relative_to(play_repo))] = digest.hexdigest()
    return manifest


def capture_baseline(
    play_repo: Path,
    baseline_path: Path,
    *,
    run_cmd: RunCmd = _default_run_cmd,
) -> None:
    """Capture the baseline this run will be checked against. Idempotent --
    safe to call again on resume; it simply overwrites the same file with the
    same content if the Play repo hasn't changed since the first capture."""
    mode = "git" if _is_git_repo_root(play_repo, run_cmd=run_cmd) else "manifest"
    payload: dict[str, Any] = {"mode": mode}
    if mode == "manifest":
        payload["manifest"] = build_manifest(play_repo)
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_path.write_text(json.dumps(payload), encoding="utf-8")


def check(
    play_repo: Path,
    baseline_path: Path,
    *,
    run_cmd: RunCmd = _default_run_cmd,
) -> GuardStatus:
    """Returns "clean" | "tampered" | "error". A missing baseline file, an
    unreadable one, or a mode mismatch between capture and check time are all
    "error" -- never treated as "nothing to compare against, so clean"."""
    if not baseline_path.is_file():
        return "error"
    try:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "error"

    mode = baseline.get("mode")
    if mode == "git":
        if not _is_git_repo_root(play_repo, run_cmd=run_cmd):
            # The repo was git at capture time and isn't now (e.g. .git
            # deleted) -- that is itself tamper-shaped, not a silent pass.
            return "error"
        return _git_status_clean(play_repo, run_cmd=run_cmd)
    if mode == "manifest":
        baseline_manifest = baseline.get("manifest")
        if not isinstance(baseline_manifest, dict):
            return "error"
        current_manifest = build_manifest(play_repo)
        return "clean" if current_manifest == baseline_manifest else "tampered"
    return "error"
