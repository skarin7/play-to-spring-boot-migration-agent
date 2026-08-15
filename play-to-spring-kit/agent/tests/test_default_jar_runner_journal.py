"""graph.py:_default_jar_runner journal integration (M6 Task 7): verifies the
production jar-runner implementation (the one every real run uses, as
opposed to the many test fixtures that inject a fake jar_runner directly)
writes one journal entry per migrate-app batch under
.migration/journal/<unit>.ndjson, and that a subsequent transform_node fold
picks those entries up via journal_offsets.
"""

import json
import subprocess

from agent.config import AgentConfig
from agent.graph import _default_jar_runner
from agent.tools.journal import fold_journal, journal_path


def _fake_subprocess_run(outputs):
    """Patches subprocess.run itself, not toolkit_jar.run_cmd -- run_migrate_slice's
    `runner: RunCmd = run_cmd` default is bound at function-definition time,
    so patching the toolkit_jar module attribute after the fact has no
    effect on calls that rely on that default (as _default_jar_runner's
    calls do -- it never passes its own runner)."""

    def fake_run(argv, cwd=None, capture_output=True, text=True, timeout=None):
        out_text = outputs.pop(0) if outputs else "migrate-app done: 0 files, 0 errors, 0 remaining"
        return subprocess.CompletedProcess(argv, 0, out_text, "")

    return fake_run


def test_default_jar_runner_writes_journal_per_batch(tmp_path, monkeypatch):
    play_repo = tmp_path / "play"
    play_repo.mkdir()
    spring_repo = tmp_path / "spring"
    spring_repo.mkdir()
    jar_path = tmp_path / "dev-toolkit.jar"
    jar_path.write_bytes(b"fake")

    cfg = AgentConfig(spring_repo=spring_repo, play_repo=play_repo, jar_path=jar_path)

    outputs = [
        "migrate-app done: 3 files, 0 errors, 2 remaining",
        "migrate-app done: 2 files, 0 errors, 0 remaining",
    ]
    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run(outputs))

    n_add, m_err = _default_jar_runner(cfg, "controllers")

    assert n_add == 5
    assert m_err == 0

    path = journal_path(spring_repo, "controllers")
    assert path.is_file()
    lines = [json.loads(line) for line in path.read_text().strip().split("\n") if line.strip()]
    migrated = [e for e in lines if e.get("action") == "migrated"]
    assert len(migrated) == 2
    assert [e["count"] for e in migrated] == [3, 2]


def test_default_jar_runner_journal_errors_recorded(tmp_path, monkeypatch):
    play_repo = tmp_path / "play"
    play_repo.mkdir()
    spring_repo = tmp_path / "spring"
    spring_repo.mkdir()
    jar_path = tmp_path / "dev-toolkit.jar"
    jar_path.write_bytes(b"fake")

    cfg = AgentConfig(spring_repo=spring_repo, play_repo=play_repo, jar_path=jar_path)
    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run(["migrate-app done: 1 files, 2 errors, 0 remaining"]))

    _default_jar_runner(cfg, "service")

    path = journal_path(spring_repo, "service")
    lines = [json.loads(line) for line in path.read_text().strip().split("\n") if line.strip()]
    compiled = [e for e in lines if e.get("action") == "compiled"]
    assert compiled == [{"unit": "service", "action": "compiled", "error_count": 2}]


def test_default_jar_runner_journal_fold_survives_across_calls(tmp_path, monkeypatch):
    """Simulates the crash-recovery scenario: two separate _default_jar_runner
    calls (e.g. resumed after a crash between them), each fold only sees the
    entries written since the last fold."""
    play_repo = tmp_path / "play"
    play_repo.mkdir()
    spring_repo = tmp_path / "spring"
    spring_repo.mkdir()
    jar_path = tmp_path / "dev-toolkit.jar"
    jar_path.write_bytes(b"fake")

    cfg = AgentConfig(spring_repo=spring_repo, play_repo=play_repo, jar_path=jar_path)

    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run(["migrate-app done: 4 files, 0 errors, 0 remaining"]))
    _default_jar_runner(cfg, "controllers")

    entries_a, offsets = fold_journal(spring_repo, "controllers", {})
    assert sum(e["count"] for e in entries_a if e["action"] == "migrated") == 4

    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run(["migrate-app done: 3 files, 0 errors, 0 remaining"]))
    _default_jar_runner(cfg, "controllers")

    entries_b, offsets = fold_journal(spring_repo, "controllers", offsets)
    assert sum(e["count"] for e in entries_b if e["action"] == "migrated") == 3
    assert entries_a != entries_b  # second fold did not re-return the first batch
