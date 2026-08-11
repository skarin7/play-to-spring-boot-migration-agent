"""``java-dev-toolkit`` JAR subprocess wrapper (``inventory`` command).

Pre-flight Play-surface scan run before the first ``migrate-app`` slice transform:
classifies every Play/DI/actor import as KNOWN/UNKNOWN/PARADIGM and reports coverage,
so gaps (e.g. Akka actors) are known up front instead of discovered as a broken build.

Mirrors ``toolkit_jar.run_migrate_slice``'s injectable-runner pattern (``tools/toolkit_jar.py``)
so tests can script subprocess behavior without a real JAR or ``java`` binary.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .toolkit_jar import RunCmd, run_cmd


def run_inventory_scan(
    play_repo: Path,
    jar: Path,
    report_path: Path,
    dry_run: bool = False,
    *,
    runner: RunCmd = run_cmd,
) -> dict[str, Any] | None:
    """Invoke ``inventory`` and read back the JSON report it writes.

    Returns ``None`` on dry-run, or if the report is missing/unparseable (subprocess
    failure) -- callers should treat that as "no pre-flight signal", not fail the run.
    """
    argv = ["java", "-jar", str(jar), "inventory", "--source", str(play_repo), "--report", str(report_path)]
    runner(argv, play_repo, dry_run)
    if dry_run:
        return None
    if not report_path.is_file():
        return None
    try:
        return json.loads(report_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
