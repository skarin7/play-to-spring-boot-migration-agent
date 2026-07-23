"""Regression tests for adopting a legacy migration-status.json (M5 Task 2).

Adoption should only kick in when no langgraph checkpoint exists yet for the
thread (a genuinely fresh run) and --fresh wasn't passed. It must never fire
once a checkpoint exists, even if a status.json is also present on disk.
"""

from __future__ import annotations

import json

from langgraph.checkpoint.memory import InMemorySaver

from agent import cli
from agent.graph import RuntimeCtx, build_graph
from agent.tests.test_graph_flow import FakeCompileResult, FakeCompiler, FakeFixer, _real_clusterer


class _GraphBuilderStub:
    def __init__(self, config, ctx):
        self._config = config
        self._ctx = ctx

    def compile(self, checkpointer):
        return build_graph(self._config, self._ctx).compile(checkpointer=checkpointer)


def _write_legacy_status(tmp_path):
    status = {
        "schema_version": 2,
        "migration_units": [
            {"id": "legacy-unit", "path_prefix": "", "status": "done"},
            {"id": "legacy-unit-2", "path_prefix": "", "status": "pending"},
        ],
        "autonomous": {"total_llm_calls": 3},
        # Deliberately terminal -- must NOT be adopted (would short-circuit
        # the run at route_after_setup before anything runs).
        "run_outcome": "slice_failures",
        "run_exit_code": 5,
    }
    (tmp_path / "migration-status.json").write_text(json.dumps(status), encoding="utf-8")


def test_adopts_legacy_units_when_no_checkpoint_exists(tmp_path, monkeypatch, capsys):
    _write_legacy_status(tmp_path)
    checkpointer = InMemorySaver()
    ctx = RuntimeCtx(FakeCompiler([FakeCompileResult(0)]), FakeFixer(), _real_clusterer())
    monkeypatch.setattr(cli, "build_graph", lambda config: _GraphBuilderStub(config, ctx))
    monkeypatch.setattr(cli, "make_checkpointer", lambda config: checkpointer)

    exit_code = cli.main(["--spring-repo", str(tmp_path)])

    captured = capsys.readouterr()
    assert "adopting legacy status" in captured.err
    # Both the pre-adopted "done" unit and the newly-compiled pending one
    # count toward the total -- proves discovery was skipped in favor of
    # the adopted migration_units, not a fresh single "default" unit.
    assert "slices_done=2/2" in captured.out
    assert exit_code == 0


def test_fresh_flag_skips_adoption_even_when_status_file_exists(tmp_path, monkeypatch, capsys):
    _write_legacy_status(tmp_path)
    checkpointer = InMemorySaver()
    ctx = RuntimeCtx(FakeCompiler([FakeCompileResult(0)]), FakeFixer(), _real_clusterer())
    monkeypatch.setattr(cli, "build_graph", lambda config: _GraphBuilderStub(config, ctx))
    monkeypatch.setattr(cli, "make_checkpointer", lambda config: checkpointer)

    exit_code = cli.main(["--spring-repo", str(tmp_path), "--fresh"])

    captured = capsys.readouterr()
    assert "adopting legacy status" not in captured.err
    # Fresh discovery: one "default" unit (no play_repo configured), not the
    # two adopted legacy units.
    assert "slices_done=1/1" in captured.out
    assert exit_code == 0


def test_does_not_adopt_once_a_checkpoint_already_exists(tmp_path, monkeypatch, capsys):
    _write_legacy_status(tmp_path)
    checkpointer = InMemorySaver()
    compiler = FakeCompiler([FakeCompileResult(0), FakeCompileResult(0)])
    ctx = RuntimeCtx(compiler, FakeFixer(), _real_clusterer())
    monkeypatch.setattr(cli, "build_graph", lambda config: _GraphBuilderStub(config, ctx))
    monkeypatch.setattr(cli, "make_checkpointer", lambda config: checkpointer)

    # First run seeds a real checkpoint for this thread (adopts once, fine).
    cli.main(["--spring-repo", str(tmp_path)])
    capsys.readouterr()

    # Second run, same thread_id/checkpointer: a checkpoint now exists, so
    # adoption must not fire again even though the status.json is still on
    # disk -- sqlite stays authoritative.
    exit_code = cli.main(["--spring-repo", str(tmp_path)])
    captured = capsys.readouterr()
    assert "adopting legacy status" not in captured.err
    assert exit_code == 0


def test_malformed_status_json_warns_and_falls_back_to_fresh(tmp_path, monkeypatch, capsys):
    (tmp_path / "migration-status.json").write_text("{not valid json", encoding="utf-8")
    checkpointer = InMemorySaver()
    ctx = RuntimeCtx(FakeCompiler([FakeCompileResult(0)]), FakeFixer(), _real_clusterer())
    monkeypatch.setattr(cli, "build_graph", lambda config: _GraphBuilderStub(config, ctx))
    monkeypatch.setattr(cli, "make_checkpointer", lambda config: checkpointer)

    exit_code = cli.main(["--spring-repo", str(tmp_path)])

    captured = capsys.readouterr()
    assert "warning: could not read" in captured.err
    assert "slices_done=1/1" in captured.out  # fresh single "default" unit
    assert exit_code == 0
