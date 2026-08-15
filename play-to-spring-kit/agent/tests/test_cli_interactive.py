"""Regression tests for --interactive's resume-on-restart fix.

cli.main() must never call graph.invoke(initial, ...) on a thread that is
already paused at human_gate's interrupt() -- that silently discards the
pending decision and restarts the whole graph from START. These tests drive
cli.main() itself (not graph.invoke directly, like test_human_gate.py does)
so the exact bug that was found and fixed (a real human rerunning the
documented command after a dropped session) is what's under test.
"""

from __future__ import annotations

import builtins

from langgraph.checkpoint.memory import InMemorySaver

from agent import cli
from agent.graph import RuntimeCtx, build_graph
from agent.tests.test_graph_flow import FakeCompileResult, FakeCompiler, FakeFixer, _real_clusterer


class _GraphBuilderStub:
    """Mimics build_graph(config).compile(checkpointer=...) but wires the
    REAL graph to a FakeCompiler/FakeFixer ctx, so infra-error routing
    through human_gate is exercised for real (not mocked away)."""

    def __init__(self, config, ctx):
        self._config = config
        self._ctx = ctx

    def compile(self, checkpointer):
        return build_graph(self._config, self._ctx).compile(checkpointer=checkpointer)


def _raise_eof(*_args, **_kwargs):
    raise EOFError()


def test_eof_at_prompt_exits_cleanly_and_stays_resumable(tmp_path, monkeypatch, capsys):
    checkpointer = InMemorySaver()
    ctx = RuntimeCtx(
        FakeCompiler([FakeCompileResult(1, [], log="fatal jvm crash", infra=True)]),
        FakeFixer(),
        _real_clusterer(),
    )
    monkeypatch.setattr(cli, "build_graph", lambda config: _GraphBuilderStub(config, ctx))
    monkeypatch.setattr(cli, "make_checkpointer", lambda config: checkpointer)
    monkeypatch.setattr(builtins, "input", _raise_eof)

    exit_code = cli.main(["--spring-repo", str(tmp_path), "--interactive"])

    assert exit_code == cli.EXIT_AWAITING_HUMAN_INPUT
    captured = capsys.readouterr()
    assert "resumable" in captured.err.lower()


def test_rerun_after_dropped_session_resumes_not_restarts(tmp_path, monkeypatch, capsys):
    checkpointer = InMemorySaver()
    compiler = FakeCompiler([FakeCompileResult(1, [], log="fatal jvm crash", infra=True)])
    ctx = RuntimeCtx(compiler, FakeFixer(), _real_clusterer())
    monkeypatch.setattr(cli, "build_graph", lambda config: _GraphBuilderStub(config, ctx))
    monkeypatch.setattr(cli, "make_checkpointer", lambda config: checkpointer)

    # "Process 1": the human never answers (dropped terminal / killed process).
    monkeypatch.setattr(builtins, "input", _raise_eof)
    exit1 = cli.main(["--spring-repo", str(tmp_path), "--interactive"])
    assert exit1 == cli.EXIT_AWAITING_HUMAN_INPUT
    assert compiler.calls == 1  # only the one infra-erroring compile happened

    # "Process 2": the human reruns the exact same command and answers this time.
    monkeypatch.setattr(builtins, "input", lambda *a, **k: "abort")
    exit2 = cli.main(["--spring-repo", str(tmp_path), "--interactive"])
    assert exit2 == 5  # EXIT_INFRASTRUCTURE
    # Critically: compile was NOT re-run from scratch on the rerun -- still
    # exactly 1 call. Before the fix this would be 2 (bootstrap/inventory/
    # slice-pipeline/compile all silently re-executed from START).
    assert compiler.calls == 1


def test_unrecognized_resume_input_warns_and_defaults_to_abort(tmp_path, monkeypatch, capsys):
    checkpointer = InMemorySaver()
    ctx = RuntimeCtx(
        FakeCompiler([FakeCompileResult(1, [], log="fatal jvm crash", infra=True)]),
        FakeFixer(),
        _real_clusterer(),
    )
    monkeypatch.setattr(cli, "build_graph", lambda config: _GraphBuilderStub(config, ctx))
    monkeypatch.setattr(cli, "make_checkpointer", lambda config: checkpointer)
    monkeypatch.setattr(builtins, "input", lambda *a, **k: "yes please")

    exit_code = cli.main(["--spring-repo", str(tmp_path), "--interactive"])

    assert exit_code == 5  # EXIT_INFRASTRUCTURE -- ambiguous input defaults to abort
    captured = capsys.readouterr()
    assert "unrecognized input" in captured.err.lower()
