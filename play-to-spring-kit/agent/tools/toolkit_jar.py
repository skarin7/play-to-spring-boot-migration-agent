"""``java-dev-toolkit`` JAR subprocess wrapper (``migrate-app`` command).

Copied from scripts/migration_orchestrator.py for byte-parity, decoupled from
the legacy module:
  run_cmd            (orchestrator :1452)
  run_migrate_slice   (orchestrator :1892)
  migrate_until_done  (orchestrator :1996)

``run_cmd`` is injectable so tests can script subprocess output without a real
JAR or ``java`` binary.
"""

from __future__ import annotations

import logging
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable

from ..inventory import normalize_path_prefix

LOG = logging.getLogger("agent.tools.toolkit_jar")

MIGRATE_DONE_RE = re.compile(
    r"migrate-app done:\s*(\d+)\s*files,\s*(\d+)\s*errors,\s*(\d+)\s*remaining",
    re.IGNORECASE,
)

RunCmd = Callable[[list[str], Path | None, bool], subprocess.CompletedProcess]


def run_cmd(argv: list[str], cwd: Path | None, dry_run: bool) -> subprocess.CompletedProcess:
    if dry_run:
        print("[dry-run]", " ".join(argv), file=sys.stderr)
        return subprocess.CompletedProcess(argv, 0, "", "")
    return subprocess.run(argv, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=None)


def parse_migrate_output(text: str) -> tuple[int, int, int] | None:
    m = MIGRATE_DONE_RE.search(text)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def run_migrate_slice(
    play_repo: Path,
    jar: Path,
    spring_repo: Path,
    batch_size: int | None,
    dry_run: bool,
    *,
    path_prefix: str | None = None,
    runner: RunCmd = run_cmd,
) -> tuple[str, int, int, int]:
    """Invoke ``migrate-app`` scoped by an app-relative ``--path-prefix``."""
    argv = ["java", "-jar", str(jar), "migrate-app", "--target", str(spring_repo)]
    if path_prefix is not None:
        px = normalize_path_prefix(str(path_prefix))
        if px:
            argv.extend(["--path-prefix", px])
    if batch_size:
        argv.extend(["--batch-size", str(batch_size)])
    proc = runner(argv, play_repo, dry_run)
    out = (proc.stdout or "") + (proc.stderr or "")
    parsed = parse_migrate_output(out)
    if parsed is None:
        return out, 0, 0, -1
    n, m, r = parsed
    return out, n, m, r


def migrate_until_done(
    play_repo: Path,
    jar: Path,
    spring_repo: Path,
    batch_size: int | None,
    dry_run: bool,
    *,
    path_prefix: str | None = None,
    runner: RunCmd = run_cmd,
) -> tuple[int, int]:
    """Returns (total_files_processed, total_errors)."""
    total_n = 0
    total_m = 0
    prev_r = None
    while True:
        _, n, m, r = run_migrate_slice(
            play_repo, jar, spring_repo, batch_size, dry_run, path_prefix=path_prefix, runner=runner
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
            LOG.warning("migrate-app remaining did not decrease (%s -> %s), stopping.", prev_r, r)
            break
        prev_r = r
    return total_n, total_m
