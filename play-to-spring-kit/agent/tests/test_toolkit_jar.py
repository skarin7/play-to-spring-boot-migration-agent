"""tools/toolkit_jar.py tests with a scripted fake subprocess runner (no real JAR/java)."""

from pathlib import Path

from agent.tools import toolkit_jar


def fake_runner(outputs):
    calls = []

    def runner(argv, cwd, dry_run):
        calls.append(argv)
        text = outputs.pop(0) if outputs else "migrate-app done: 0 files, 0 errors, 0 remaining"

        class Result:
            stdout = text
            stderr = ""

        return Result()

    return runner, calls


def test_run_migrate_slice_parses_output(tmp_path):
    runner, calls = fake_runner(["migrate-app done: 3 files, 1 errors, 5 remaining"])
    out, n, m, r = toolkit_jar.run_migrate_slice(
        tmp_path, Path("dev-toolkit.jar"), tmp_path, None, False, path_prefix="controllers", runner=runner
    )
    assert (n, m, r) == (3, 1, 5)
    assert "--path-prefix" in calls[0] and "controllers" in calls[0]


def test_run_migrate_slice_unparseable_output_returns_negative_remaining(tmp_path):
    runner, _ = fake_runner(["garbage output, no summary line"])
    _, n, m, r = toolkit_jar.run_migrate_slice(tmp_path, Path("j.jar"), tmp_path, None, False, runner=runner)
    assert (n, m, r) == (0, 0, -1)


def test_migrate_until_done_loops_until_remaining_zero(tmp_path):
    outputs = [
        "migrate-app done: 2 files, 0 errors, 4 remaining",
        "migrate-app done: 2 files, 0 errors, 2 remaining",
        "migrate-app done: 2 files, 0 errors, 0 remaining",
    ]
    runner, calls = fake_runner(list(outputs))
    total_n, total_m = toolkit_jar.migrate_until_done(tmp_path, Path("j.jar"), tmp_path, None, False, runner=runner)
    assert total_n == 6
    assert total_m == 0
    assert len(calls) == 3


def test_migrate_until_done_stops_when_remaining_does_not_decrease(tmp_path):
    outputs = [
        "migrate-app done: 1 files, 0 errors, 5 remaining",
        "migrate-app done: 0 files, 0 errors, 5 remaining",  # stalled
    ]
    runner, calls = fake_runner(list(outputs))
    total_n, _ = toolkit_jar.migrate_until_done(tmp_path, Path("j.jar"), tmp_path, None, False, runner=runner)
    assert total_n == 1
    assert len(calls) == 2  # stops after the stalled round, doesn't loop forever


def test_migrate_until_done_dry_run_runs_once(tmp_path):
    runner, calls = fake_runner([])
    toolkit_jar.migrate_until_done(tmp_path, Path("j.jar"), tmp_path, None, True, runner=runner)
    assert len(calls) == 1
