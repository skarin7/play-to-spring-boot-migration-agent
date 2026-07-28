"""Deterministic setup subprocess wrappers: toolkit JAR build, kit setup.sh, config export.

Copied from scripts/migration_orchestrator.py for byte-parity, decoupled from
the legacy module:
  scripts_dir / kit_root / default_dev_toolkit_root / resolved_dev_toolkit_root (:150-171)
  find_packaged_toolkit_jar   (:174)
  ensure_jar_in_kit_lib       (:193)
  run_setup_sh                (:406)
  run_export_play_conf        (:443)

``runner`` is injectable (same convention as tools/toolkit_jar.py) so tests can
script subprocess results without a real JDK/Maven/bash/pyhocon.
"""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

LOG = logging.getLogger("agent.tools.setup_ops")

RunCmd = Callable[[list[str], Path | None, bool], subprocess.CompletedProcess]


def run_cmd(argv: list[str], cwd: Path | None, dry_run: bool) -> subprocess.CompletedProcess:
    if dry_run:
        print("[dry-run]", " ".join(shlex.quote(a) for a in argv), file=sys.stderr)
        return subprocess.CompletedProcess(argv, 0, "", "")
    return subprocess.run(argv, cwd=str(cwd) if cwd else None, capture_output=True, text=True)


def scripts_dir() -> Path:
    """Directory containing scripts/ (setup.sh, conf_to_application_properties.py)."""
    return Path(__file__).resolve().parent.parent.parent / "scripts"


def kit_root() -> Path:
    """play-to-spring-kit root (lib/, skills/, config/, ...)."""
    return scripts_dir().parent


def default_dev_toolkit_root() -> Path:
    """java-dev-toolkit next to play-to-spring-kit (monorepo layout)."""
    return kit_root().parent / "java-dev-toolkit"


def resolved_dev_toolkit_root(cli_path: Path | None) -> Path:
    if cli_path is not None:
        return cli_path.expanduser().resolve(strict=False)
    env = os.environ.get("JAVA_DEV_TOOLKIT_ROOT")
    if env:
        return Path(env).expanduser().resolve(strict=False)
    return default_dev_toolkit_root()


def find_packaged_toolkit_jar(toolkit_root: Path) -> Path | None:
    """Shaded dev-toolkit-*.jar under target/ (exclude sources/javadoc)."""
    td = toolkit_root / "target"
    if not td.is_dir():
        return None
    jars = [p for p in td.glob("dev-toolkit-*.jar") if p.is_file()]
    if not jars:
        return None

    def sort_key(p: Path) -> tuple[int, str]:
        n = p.name.lower()
        return (1, p.name) if ("sources" in n or "javadoc" in n) else (0, p.name)

    jars.sort(key=sort_key)
    return jars[0]


def ensure_jar_in_kit_lib(
    *,
    skip_build: bool,
    toolkit_root: Path,
    dry_run: bool,
    runner: RunCmd = run_cmd,
) -> tuple[bool, str]:
    """
    Build java-dev-toolkit and copy the JAR to play-to-spring-kit/lib/, or verify
    lib/ already contains a JAR when skip_build is True.

    Returns (ok, message).
    """
    lib_dir = kit_root() / "lib"
    if skip_build:
        existing = sorted(lib_dir.glob("*.jar"))
        if not existing:
            return False, (
                f"--skip-build-toolkit set but no JAR found in {lib_dir}. "
                "Build the toolkit and copy dev-toolkit-*.jar there, or omit --skip-build-toolkit."
            )
        return True, f"Using existing JAR in {lib_dir}: {existing[0].name}"

    if not toolkit_root.is_dir():
        return False, (
            f"java-dev-toolkit directory not found: {toolkit_root}. Clone the full repo "
            "(sibling layout), set JAVA_DEV_TOOLKIT_ROOT / --toolkit-root, or use "
            f"--skip-build-toolkit if the JAR is already under {lib_dir}/."
        )
    if not (toolkit_root / "pom.xml").is_file():
        return False, f"not a Maven project (missing pom.xml): {toolkit_root}"

    if dry_run:
        return True, f"[dry-run] mvn package -DskipTests in {toolkit_root}; copy dev-toolkit-*.jar -> {lib_dir}/"

    proc = runner(["mvn", "package", "-DskipTests"], toolkit_root, False)
    if proc.returncode != 0:
        return False, "mvn package failed (dev-toolkit build)."

    jar = find_packaged_toolkit_jar(toolkit_root)
    if jar is None:
        return False, f"no dev-toolkit-*.jar under {toolkit_root / 'target'} after build."

    lib_dir.mkdir(parents=True, exist_ok=True)
    dest = lib_dir / jar.name
    shutil.copy2(jar, dest)
    return True, f"Installed {jar.name} -> {dest}"


def run_setup_sh(
    play_repo: Path,
    workspace_dir: Path,
    spring_name: str | None,
    dry_run: bool,
    runner: RunCmd = run_cmd,
) -> tuple[bool, str]:
    """Run the kit's install/bootstrap shell script (idempotent)."""
    setup_sh = scripts_dir() / "setup.sh"
    if not setup_sh.is_file():
        return False, f"kit install script missing at {setup_sh}"
    # langgraph engine never shells out to cursor-agent, so skip installing
    # .cursor/skills, .cursor/settings.json, and the cursor-agent-specific
    # "Next steps" block that setup.sh prints (that's legacy-engine-only).
    cmd = [
        "bash", str(setup_sh), str(play_repo),
        "--workspace", str(workspace_dir), "--skip-cursor-setup",
    ]
    if spring_name:
        cmd.extend(["--spring-name", spring_name])
    if dry_run:
        return True, f"[dry-run] {' '.join(shlex.quote(a) for a in cmd)}"
    proc = runner(cmd, kit_root(), False)
    if proc.returncode != 0:
        return False, f"setup.sh exited {proc.returncode}: {(proc.stderr or '')[-2000:]}"
    return True, (proc.stdout or "")


def run_export_play_conf(
    play_repo: Path,
    spring_repo: Path,
    extra_strip_prefixes: list[str],
    dry_run: bool,
    runner: RunCmd = run_cmd,
) -> tuple[bool, str]:
    """
    Optionally flatten Play conf/application.conf into Spring application.properties.

    Non-fatal by design (mirrors legacy): missing conf file, missing converter
    script, or converter errors just return ok=True with an explanatory message.
    """
    conf_path = play_repo / "conf" / "application.conf"
    if not conf_path.is_file():
        return True, f"skip (no {conf_path})"
    script = scripts_dir() / "conf_to_application_properties.py"
    if not script.is_file():
        return True, f"converter missing at {script} (skipped)"
    out_path = spring_repo / "src" / "main" / "resources" / "application.properties"
    cmd = [sys.executable, str(script), "-i", str(conf_path), "-o", str(out_path)]
    for raw in ["akka."] + list(extra_strip_prefixes or []):
        p = raw.strip()
        if p:
            cmd.extend(["--strip-prefix", p])
    if dry_run:
        return True, f"[dry-run] {' '.join(shlex.quote(a) for a in cmd)}"
    proc = runner(cmd, kit_root(), False)
    if proc.returncode == 2:
        return True, "pyhocon not installed (skipped)"
    if proc.returncode != 0:
        return True, f"converter exited {proc.returncode} (continuing)"
    return True, (proc.stdout or "")


class SetupOps:
    """Config-driven adapter over the module-level functions above (injectable via RuntimeCtx)."""

    def ensure_jar(self, config) -> tuple[bool, str]:
        if config.play_repo is None:
            return True, "no play_repo configured, skip toolkit build"
        toolkit_root = resolved_dev_toolkit_root(config.toolkit_root)
        return ensure_jar_in_kit_lib(
            skip_build=config.skip_build_toolkit, toolkit_root=toolkit_root, dry_run=config.dry_run
        )

    def install(self, config) -> tuple[bool, str]:
        if config.play_repo is None:
            return True, "no play_repo configured, skip setup.sh"
        return run_setup_sh(config.play_repo, config.workspace_dir, config.spring_name, config.dry_run)

    def export_conf(self, config) -> tuple[bool, str]:
        if config.play_repo is None or not config.export_play_conf:
            return True, "skip"
        return run_export_play_conf(
            config.play_repo, config.spring_repo, config.conf_strip_prefixes, config.dry_run
        )
