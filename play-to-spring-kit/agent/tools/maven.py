"""``mvn spring-boot:run`` subprocess wrapper (M4 runtime verification loop).

Unlike toolkit_jar.py/setup_ops.py's ``RunCmd`` (blocks until the process
exits), detecting "has the app started yet" needs to watch output *while the
process runs* -- Spring Boot never exits on success, it just keeps serving.
So this wraps a long-lived ``Popen`` instead of ``subprocess.run``, reading
its stdout on a background thread with a wall-clock deadline, and always
terminates the process before returning.

``popen_factory`` is the test seam (same convention as ``runner`` elsewhere
in ``tools/``): tests inject a fake object shaped like ``Popen`` instead of
spawning real Maven/Java.
"""

from __future__ import annotations

import queue
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

STARTED_RE = re.compile(r"Started \S+ in [\d.]+ seconds")

LOG_TAIL_CHARS = 4000

PopenFactory = Callable[[list[str], Path], Any]


@dataclass
class BootResult:
    started: bool
    log_tail: str


@dataclass
class TestResult:
    returncode: int
    log_tail: str
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0

    @property
    def all_passed(self) -> bool:
        return self.returncode == 0 and self.failed == 0 and self.errors == 0


# Maven surefire's own summary line, e.g.:
# "Tests run: 12, Failures: 1, Errors: 0, Skipped: 2"
_SUREFIRE_SUMMARY_RE = re.compile(
    r"Tests run:\s*(\d+),\s*Failures:\s*(\d+),\s*Errors:\s*(\d+),\s*Skipped:\s*(\d+)"
)


def _parse_test_summary(log: str) -> tuple[int, int, int, int]:
    """Sums every "Tests run: N, Failures: N, Errors: N, Skipped: N" line in
    the log (surefire prints one per test class plus a final aggregate) --
    the LAST match is the aggregate Maven itself prints, so use that one
    rather than summing (which would double-count against the per-class
    lines)."""
    matches = _SUREFIRE_SUMMARY_RE.findall(log)
    if not matches:
        return 0, 0, 0, 0
    run, failures, errors, skipped = (int(x) for x in matches[-1])
    passed = run - failures - errors - skipped
    return passed, failures, errors, skipped


def run_mvn_test(
    spring_repo: Path,
    timeout_sec: int,
    dry_run: bool,
    *,
    cmd: list[str] = ("mvn", "-q", "-B", "test"),
    run_cmd: Callable[[list[str], Path, int], Any] | None = None,
) -> TestResult:
    """T4 (M6 Task 10): runs `mvn test`, bounded by timeout_sec, and parses
    surefire's own summary line rather than trying to interpret returncode
    alone (a returncode of 1 is ambiguous between "tests failed" and "build
    itself broke" without the summary line to disambiguate)."""
    if dry_run:
        return TestResult(returncode=0, log_tail="[dry-run] mvn test skipped", passed=0)

    def _default_run_cmd(argv: list[str], cwd: Path, timeout: int) -> Any:
        return subprocess.run(
            argv, cwd=str(cwd), capture_output=True, text=True, timeout=timeout, errors="replace"
        )

    runner = run_cmd or _default_run_cmd
    try:
        proc = runner(list(cmd), spring_repo, timeout_sec)
    except subprocess.TimeoutExpired as exc:
        log = (exc.stdout or "") + (exc.stderr or "")
        return TestResult(returncode=-1, log_tail=log[-LOG_TAIL_CHARS:])

    log = (proc.stdout or "") + (proc.stderr or "")
    passed, failed, errors, skipped = _parse_test_summary(log)
    return TestResult(
        returncode=proc.returncode,
        log_tail=log[-LOG_TAIL_CHARS:],
        passed=passed,
        failed=failed,
        errors=errors,
        skipped=skipped,
    )


def _default_popen_factory(argv: list[str], cwd: Path) -> subprocess.Popen:
    return subprocess.Popen(
        argv,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        bufsize=1,
    )


def _reader_thread(stdout: Any, out_queue: "queue.Queue[str | None]") -> None:
    try:
        for line in stdout:
            out_queue.put(line)
    finally:
        out_queue.put(None)  # sentinel: stream ended (process exited or closed stdout)


def _terminate(proc: Any) -> None:
    """Always stop the process, regardless of outcome (never leave mvn/java orphaned)."""
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:
            pass


def run_spring_boot(
    spring_repo: Path,
    timeout_sec: int,
    dry_run: bool,
    *,
    cmd: list[str] = ("mvn", "-q", "spring-boot:run"),
    popen_factory: PopenFactory | None = None,
) -> BootResult:
    if dry_run:
        return BootResult(started=True, log_tail="[dry-run] mvn spring-boot:run skipped")

    factory = popen_factory or _default_popen_factory
    proc = factory(list(cmd), spring_repo)

    out_queue: "queue.Queue[str | None]" = queue.Queue()
    thread = threading.Thread(target=_reader_thread, args=(proc.stdout, out_queue), daemon=True)
    thread.start()

    deadline = time.time() + timeout_sec
    log_tail = ""
    started = False
    try:
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            try:
                line = out_queue.get(timeout=remaining)
            except queue.Empty:
                break  # deadline reached without EOF or a started line
            if line is None:
                break  # process exited / closed stdout without ever starting
            log_tail = (log_tail + line)[-LOG_TAIL_CHARS:]
            if STARTED_RE.search(line):
                started = True
                break
    finally:
        _terminate(proc)

    return BootResult(started=started, log_tail=log_tail)
