"""tools/maven.py:run_mvn_test unit tests (M6 Task 10, T4). No real mvn
subprocess ever spawned -- run_cmd is the injectable seam, same convention
as toolkit_jar.py's runner and setup_ops.py's run_cmd."""

from __future__ import annotations

import subprocess

from agent.tools.maven import run_mvn_test


class FakeCompletedProcess:
    def __init__(self, returncode: int, stdout: str, stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _fake_run_cmd(returncode: int, stdout: str):
    calls = []

    def run_cmd(argv, cwd, timeout):
        calls.append((argv, cwd, timeout))
        return FakeCompletedProcess(returncode, stdout)

    return run_cmd, calls


def test_all_tests_pass(tmp_path):
    log = "Tests run: 12, Failures: 0, Errors: 0, Skipped: 0\nBUILD SUCCESS\n"
    run_cmd, calls = _fake_run_cmd(0, log)

    result = run_mvn_test(tmp_path, 600, False, run_cmd=run_cmd)

    assert result.all_passed is True
    assert result.passed == 12
    assert result.failed == 0
    assert result.errors == 0
    assert result.skipped == 0
    assert calls[0][1] == tmp_path
    assert calls[0][2] == 600


def test_some_tests_fail(tmp_path):
    log = "Tests run: 12, Failures: 2, Errors: 1, Skipped: 1\nBUILD FAILURE\n"
    run_cmd, _ = _fake_run_cmd(1, log)

    result = run_mvn_test(tmp_path, 600, False, run_cmd=run_cmd)

    assert result.all_passed is False
    assert result.passed == 8
    assert result.failed == 2
    assert result.errors == 1
    assert result.skipped == 1


def test_uses_last_summary_line_not_sum_of_all(tmp_path):
    """Surefire prints one summary per test class plus a final aggregate --
    using anything but the LAST line would double-count."""
    log = (
        "Tests run: 5, Failures: 0, Errors: 0, Skipped: 0\n"  # per-class line
        "Tests run: 7, Failures: 1, Errors: 0, Skipped: 0\n"  # per-class line
        "Tests run: 12, Failures: 1, Errors: 0, Skipped: 0\n"  # aggregate (last)
    )
    run_cmd, _ = _fake_run_cmd(1, log)

    result = run_mvn_test(tmp_path, 600, False, run_cmd=run_cmd)

    assert result.passed == 11
    assert result.failed == 1


def test_no_summary_line_returns_zeros_not_crash(tmp_path):
    run_cmd, _ = _fake_run_cmd(1, "some unrelated build failure, no surefire output\n")

    result = run_mvn_test(tmp_path, 600, False, run_cmd=run_cmd)

    assert result.passed == 0
    assert result.failed == 0
    assert result.all_passed is False  # returncode != 0


def test_dry_run_never_calls_run_cmd(tmp_path):
    run_cmd, calls = _fake_run_cmd(0, "")

    result = run_mvn_test(tmp_path, 600, True, run_cmd=run_cmd)

    assert calls == []
    assert result.all_passed is True


def test_timeout_returns_negative_returncode_not_raise(tmp_path):
    def run_cmd(argv, cwd, timeout):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=timeout, output="partial log")

    result = run_mvn_test(tmp_path, 1, False, run_cmd=run_cmd)

    assert result.returncode == -1
    assert result.all_passed is False


def test_log_tail_truncated_to_last_chars(tmp_path):
    huge_log = "x" * 10_000 + "Tests run: 1, Failures: 0, Errors: 0, Skipped: 0\n"
    run_cmd, _ = _fake_run_cmd(0, huge_log)

    result = run_mvn_test(tmp_path, 600, False, run_cmd=run_cmd)

    assert len(result.log_tail) <= 4000
