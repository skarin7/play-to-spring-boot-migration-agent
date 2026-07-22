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
