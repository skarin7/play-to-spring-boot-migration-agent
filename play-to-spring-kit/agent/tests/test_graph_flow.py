"""Graph routing tests with a scripted fake compiler and fake LLM (no network)."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from agent.config import AgentConfig
from agent.graph import RuntimeCtx, build_graph, recursion_limit
from agent.state import EXIT_BUDGET_EXHAUSTED, EXIT_OK


@dataclass
class FakeCompileResult:
    returncode: int
    errors: list[dict[str, Any]] = field(default_factory=list)
    log: str = ""
    infra: bool = False

    @property
    def is_infrastructure_error(self) -> bool:
        return self.infra


class FakeCompiler:
    def __init__(self, results: list[FakeCompileResult]):
        self.results = list(results)
        self.calls = 0
        self.changed_files_per_call: list[Any] = []

    def compile(self, changed_files=None):
        self.calls += 1
        self.changed_files_per_call.append(changed_files)
        return self.results.pop(0) if self.results else FakeCompileResult(0)


@dataclass
class FakeFixResult:
    fixed_count: int = 0
    unresolved: list = field(default_factory=list)
    det_fix_log: list = field(default_factory=list)


class FakeFixer:
    def __init__(self, fixed_counts: list[int] | None = None, backed_up_rounds: list[dict] | None = None):
        self.fixed_counts = list(fixed_counts or [])
        self.backed_up_rounds = list(backed_up_rounds or [])
        self._backed_up: dict = {}

    def run(self, errors):
        fixed = self.fixed_counts.pop(0) if self.fixed_counts else 0
        self._backed_up = self.backed_up_rounds.pop(0) if self.backed_up_rounds else {}
        return FakeFixResult(fixed_count=fixed, unresolved=errors, det_fix_log=[])

    def revert_bad_fixes(self, errors):
        return []

    def delete_bak_files(self):
        pass


class FakeToolModel:
    """Duck-typed chat model: returns scripted AIMessages, tools bound as no-op."""

    def __init__(self, responses):
        self.responses = list(responses)

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        return self.responses.pop(0)


def make_config(tmp_path, **overrides) -> AgentConfig:
    cfg = AgentConfig(spring_repo=tmp_path)
    cfg.api_key = "test-key"
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def run(graph_builder_cfg, ctx):
    graph = build_graph(graph_builder_cfg, ctx).compile()
    initial = {"spring_repo": str(graph_builder_cfg.spring_repo), "slice_id": "t",
               "retry_count": 0, "total_llm_calls": 0}
    return graph.invoke(initial, config={"recursion_limit": recursion_limit(graph_builder_cfg)})


ERR = {"file": "A.java", "line": 3, "message": "cannot find symbol X"}


def test_clean_compile_is_success(tmp_path):
    cfg = make_config(tmp_path)
    ctx = RuntimeCtx(FakeCompiler([FakeCompileResult(0)]), FakeFixer(), _real_clusterer())
    final = run(cfg, ctx)
    assert final["outcome"] == "success"
    assert final["exit_code"] == EXIT_OK


def test_infrastructure_error_halts(tmp_path):
    cfg = make_config(tmp_path)
    ctx = RuntimeCtx(
        FakeCompiler([FakeCompileResult(1, [], log="fatal error compiling", infra=True)]),
        FakeFixer(),
        _real_clusterer(),
    )
    final = run(cfg, ctx)
    assert final["outcome"] == "infrastructure_error"
    assert final["exit_code"] == 5


def test_det_fix_loop_reaches_green_without_llm(tmp_path):
    # round 1: errors, fixer fixes 1 -> recompile -> green. No LLM involved.
    cfg = make_config(tmp_path)
    compiler = FakeCompiler([FakeCompileResult(1, [ERR]), FakeCompileResult(0)])
    ctx = RuntimeCtx(compiler, FakeFixer([1]), _real_clusterer())
    final = run(cfg, ctx)
    assert final["outcome"] == "success"
    assert final["total_llm_calls"] == 0
    # +1 for the M2 cross-module verify_node compile pass after all slices are done.
    assert compiler.calls == 3


def test_llm_round_then_green(tmp_path):
    # det fixer fixes nothing -> cluster -> guard -> agent -> recompile green
    cfg = make_config(tmp_path)
    compiler = FakeCompiler([FakeCompileResult(1, [ERR]), FakeCompileResult(0)])
    ctx = RuntimeCtx(
        compiler,
        FakeFixer([0]),
        _real_clusterer(),
        model_override=FakeToolModel([AIMessage(content="fixed it")]),
    )
    final = run(cfg, ctx)
    assert final["outcome"] == "success"
    assert final["total_llm_calls"] == 1
    assert final["retry_count"] == 1


def test_budget_zero_halts_with_exit_4(tmp_path):
    cfg = make_config(tmp_path, max_total_llm_calls=0)
    compiler = FakeCompiler([FakeCompileResult(1, [ERR])])
    ctx = RuntimeCtx(compiler, FakeFixer([0]), _real_clusterer())
    final = run(cfg, ctx)
    assert final["outcome"] == "budget_exhausted"
    assert final["exit_code"] == EXIT_BUDGET_EXHAUSTED


def test_looping_detected_and_signatures_excluded(tmp_path):
    # Same errors twice with an LLM round between -> fingerprints repeat -> looping
    cfg = make_config(tmp_path)
    compiler = FakeCompiler([FakeCompileResult(1, [ERR]), FakeCompileResult(1, [ERR])])
    ctx = RuntimeCtx(
        compiler,
        FakeFixer([0, 0]),
        _real_clusterer(),
        model_override=FakeToolModel([AIMessage(content="tried"), AIMessage(content="tried")]),
    )
    final = run(cfg, ctx)
    assert final["outcome"] == "looping"
    assert final["excluded_error_signatures"] == ["A.java:3:cannot find symbol X"]
    assert final["total_llm_calls"] == 1  # second round blocked by loop detection


def test_no_api_key_halts_with_exit_2(tmp_path):
    cfg = make_config(tmp_path)
    cfg.api_key = None
    compiler = FakeCompiler([FakeCompileResult(1, [ERR])])
    ctx = RuntimeCtx(compiler, FakeFixer([0]), _real_clusterer())
    final = run(cfg, ctx)
    assert final["outcome"] == "no_llm"
    assert final["exit_code"] == 2


def _real_clusterer():
    from error_clusterer import ErrorClusterer

    return ErrorClusterer()


def test_timeout_fires_during_progressing_det_fix_loop(tmp_path, monkeypatch):
    """Regression: a deterministic fixer that always reports progress must
    still be caught by timeout_layer_mins mid-loop, matching the legacy
    top-of-loop check (migration_orchestrator.py:2531) instead of only
    checking timeout once the fixer stalls (which it never does here)."""
    import time as time_module

    clock = {"t": 1_000_000.0}
    monkeypatch.setattr(time_module, "time", lambda: clock["t"])

    cfg = make_config(tmp_path, timeout_layer_mins=10)
    # Distinct error each round so det-fix "fixes" one and a new one appears —
    # never triggers is_looping, so without the compile_node-level timeout
    # check this would run until GraphRecursionError instead.
    errors = [{"file": "A.java", "line": i, "message": f"cannot find symbol X{i}"} for i in range(20)]
    compiler = FakeCompiler([FakeCompileResult(1, [e]) for e in errors])
    real_compile = compiler.compile

    def compile_and_advance_clock(changed_files=None):
        clock["t"] += 5 * 60  # each round "takes" 5 minutes of wall time
        return real_compile(changed_files)

    compiler.compile = compile_and_advance_clock
    ctx = RuntimeCtx(compiler, FakeFixer([1] * 20), _real_clusterer())

    graph = build_graph(cfg, ctx).compile()
    initial = {"spring_repo": str(tmp_path), "slice_id": "t", "retry_count": 0, "total_llm_calls": 0}
    final = graph.invoke(initial, config={"recursion_limit": recursion_limit(cfg)})
    assert final["outcome"] == "timeout"
    assert final["exit_code"] == EXIT_OK
    # 10-minute timeout / 5-minute rounds: halts partway through, not after all 20.
    assert 1 < compiler.calls < 20


def test_det_fix_edits_scope_next_compile_incrementally(tmp_path):
    """Regression: files the deterministic fixer touched must be passed as
    changed_files to the next compile (legacy parity: :2654-2655), so
    IncrementalCompiler can scope to the affected module."""
    cfg = make_config(tmp_path)
    touched = {Path("/repo/src/main/java/A.java"): Path("/repo/A.java.bak")}
    compiler = FakeCompiler([FakeCompileResult(1, [ERR]), FakeCompileResult(0)])
    ctx = RuntimeCtx(
        compiler,
        FakeFixer([1], backed_up_rounds=[touched]),
        _real_clusterer(),
    )
    final = run(cfg, ctx)
    assert final["outcome"] == "success"
    assert compiler.changed_files_per_call[0] is None  # first compile: nothing edited yet
    assert compiler.changed_files_per_call[1] == [Path("/repo/src/main/java/A.java")]


def test_det_fix_pom_change_forces_full_recompile(tmp_path):
    """Regression: when the pom-fix path adds a dependency, the next compile
    must be a full rebuild even if the fixer's _backed_up also has entries —
    a classpath change can't be scoped to one module (legacy parity: pom-fix
    branch never sets last_edited_files, migration_orchestrator.py:2643-2646)."""
    cfg = make_config(tmp_path)
    dep_err = {"file": "A.java", "line": 1, "message": "package com.google.common does not exist"}
    touched = {Path("/repo/src/main/java/A.java"): Path("/repo/A.java.bak")}
    compiler = FakeCompiler([FakeCompileResult(1, [dep_err]), FakeCompileResult(0)])
    fixer = FakeFixer([0], backed_up_rounds=[touched])
    ctx = RuntimeCtx(compiler, fixer, _real_clusterer())

    # try_deterministic_pom_fix needs a real pom.xml to edit.
    (tmp_path / "pom.xml").write_text(
        "<project><dependencies></dependencies></project>", encoding="utf-8"
    )
    final = run(cfg, ctx)
    assert final["outcome"] == "success"
    assert compiler.changed_files_per_call[1] is None  # forced full recompile
