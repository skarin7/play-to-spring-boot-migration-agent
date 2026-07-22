"""tools/maven.py unit tests: run_spring_boot with a FakeProcess double.

No real `mvn`/`java` subprocess is ever spawned here -- popen_factory is the
test seam (see tools/toolkit_jar.py's `runner` convention for the same idea
applied to a blocking-to-completion subprocess instead of a streaming one).
"""

from __future__ import annotations

from agent.tools.maven import BootResult, run_spring_boot


class FakeProcess:
    """Duck-typed Popen: .stdout is an iterable of scripted lines."""

    def __init__(self, lines: list[str]):
        self._lines = list(lines)
        self.stdout = iter(self._lines)
        self.terminated = False
        self.waited = False
        self.killed = False
        self.polled = False

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        self.waited = True

    def kill(self):
        self.killed = True

    def poll(self):
        self.polled = True
        return 0


class FakeFactory:
    """Records whether/how it was invoked; returns a scripted FakeProcess."""

    def __init__(self, lines: list[str]):
        self.lines = lines
        self.calls: list[tuple[list[str], object]] = []
        self.process: FakeProcess | None = None

    def __call__(self, argv, cwd):
        self.calls.append((argv, cwd))
        self.process = FakeProcess(self.lines)
        return self.process


def test_started_line_matches_and_process_terminated(tmp_path):
    factory = FakeFactory(
        [
            "Loading beans...\n",
            "Started FooApplication in 2.341 seconds (process running for 2.7)\n",
            "should never be read\n",
        ]
    )
    result = run_spring_boot(tmp_path, timeout_sec=5, dry_run=False, popen_factory=factory)
    assert result.started is True
    assert "Started FooApplication in 2.341 seconds" in result.log_tail
    assert factory.process is not None
    assert factory.process.terminated is True


def test_no_started_line_stream_ends_returns_false(tmp_path):
    factory = FakeFactory(
        [
            "Loading beans...\n",
            "Caused by: java.lang.IllegalStateException: no bean of type Foo\n",
        ]
    )
    result = run_spring_boot(tmp_path, timeout_sec=5, dry_run=False, popen_factory=factory)
    assert result.started is False
    assert "IllegalStateException" in result.log_tail
    assert factory.process is not None
    assert factory.process.terminated is True


def test_dry_run_skips_subprocess_entirely(tmp_path):
    factory = FakeFactory(["Started X in 1.0 seconds\n"])
    result = run_spring_boot(tmp_path, timeout_sec=5, dry_run=True, popen_factory=factory)
    assert result == BootResult(started=True, log_tail="[dry-run] mvn spring-boot:run skipped")
    assert factory.calls == []
    assert factory.process is None


def test_real_default_factory_confidence(tmp_path):
    """Extra confidence test: the *default* popen_factory really spawns a
    subprocess and the started-line detection works end-to-end, using a
    plain python3 script as a stand-in for `mvn spring-boot:run`."""
    script = tmp_path / "fake_mvn.py"
    script.write_text(
        "import sys, time\n"
        "print('Starting FooApplication')\n"
        "sys.stdout.flush()\n"
        "print('Started FooApplication in 0.5 seconds')\n"
        "sys.stdout.flush()\n"
        "time.sleep(5)\n",
        encoding="utf-8",
    )
    import sys

    result = run_spring_boot(
        tmp_path, timeout_sec=10, dry_run=False, cmd=[sys.executable, str(script)]
    )
    assert result.started is True
    assert "Started FooApplication in 0.5 seconds" in result.log_tail
