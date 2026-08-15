"""Graph-level Play-repo integrity guard wiring (M6 Task 2): a tampered or
unguardable Play repo halts the run regardless of phase, distinctly from
every other compile outcome. FsJail writes never touch the Play repo, so
these tests simulate the unjailed writer (setup.sh / migrate-app / signature)
that a guard-less run would miss.

Follows test_graph_flow.py's style: FakeCompiler/FakeFixer + RuntimeCtx(...)
+ build_graph(cfg, ctx).compile() + graph.invoke(...).
"""

from agent.config import AgentConfig
from agent.graph import RuntimeCtx, build_graph, recursion_limit
from agent.state import EXIT_INFRASTRUCTURE, EXIT_OK
from agent.tests.test_graph_flow import FakeCompileResult, FakeCompiler, FakeFixer, _real_clusterer
from agent.tools import play_guard


def _make_bootstrapped(spring_repo) -> None:
    (spring_repo / "pom.xml").write_text("<project/>")
    java_dir = spring_repo / "src" / "main" / "java" / "com" / "example"
    java_dir.mkdir(parents=True)
    (java_dir / "Application.java").write_text("class Application {}")
    props_dir = spring_repo / "src" / "main" / "resources"
    props_dir.mkdir(parents=True)
    (props_dir / "application.properties").write_text("")
    (spring_repo / ".migration").mkdir(parents=True, exist_ok=True)
    (spring_repo / ".migration" / "decisions.md").write_text("# Migration Decisions\n")


def make_config(tmp_path, **overrides) -> AgentConfig:
    play_repo = tmp_path / "play"
    play_repo.mkdir()
    (play_repo / "app.conf").write_text("x=1")
    spring_repo = tmp_path / "spring"
    spring_repo.mkdir()
    _make_bootstrapped(spring_repo)
    cfg = AgentConfig(spring_repo=spring_repo, play_repo=play_repo)
    cfg.api_key = "test-key"
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def run(cfg, ctx):
    graph = build_graph(cfg, ctx).compile()
    initial = {"spring_repo": str(cfg.spring_repo), "slice_id": "t", "retry_count": 0, "total_llm_calls": 0}
    return graph.invoke(initial, config={"recursion_limit": recursion_limit(cfg)})


# ----------------------------------------------------------------------
# 1. Clean Play repo throughout -> run proceeds normally, guard is
#    transparent (no setup_ops means bootstrap's baseline-capture branch
#    still runs since it's independent of ctx.setup_ops).
# ----------------------------------------------------------------------


def test_clean_play_repo_does_not_affect_run(tmp_path):
    cfg = make_config(tmp_path)
    ctx = RuntimeCtx(FakeCompiler([FakeCompileResult(0)]), FakeFixer(), _real_clusterer())
    final = run(cfg, ctx)
    assert final["run_outcome"] == "success"
    assert final["run_exit_code"] == EXIT_OK
    baseline_path = cfg.migration_dir / "play-baseline.json"
    assert baseline_path.is_file()


# ----------------------------------------------------------------------
# 2. Play repo modified between bootstrap's baseline capture and compile's
#    check -> run halts with play_repo_tampered, not treated as any other
#    slice outcome.
# ----------------------------------------------------------------------


def test_tampered_play_repo_halts_run(tmp_path, monkeypatch):
    """The full window this guards -- setup_node captures a baseline, then an
    unjailed writer (setup.sh / migrate-app / signature, none of which go
    through FsJail) tampers with Play before compile_node's next check --
    can't be reproduced in a single in-process graph.invoke without a real
    subprocess actually mutating the repo mid-run. tools/play_guard.py's own
    detection logic (git-mode/manifest-mode, the empty-stdout trap, the
    missing-baseline-is-error rule) is exhaustively covered in
    test_play_guard.py; this test verifies only the graph's routing
    response to a "tampered" verdict, via the same seam compile_node itself
    calls through (nodes.fix_loop imports `check` from tools.play_guard)."""
    cfg = make_config(tmp_path)
    ctx = RuntimeCtx(FakeCompiler([FakeCompileResult(0)]), FakeFixer(), _real_clusterer())

    monkeypatch.setattr(play_guard, "check", lambda *a, **k: "tampered")

    final = run(cfg, ctx)

    assert final["run_outcome"] == "play_repo_tampered"
    assert final["run_exit_code"] == EXIT_INFRASTRUCTURE


# ----------------------------------------------------------------------
# 3. Guard disabled via config -> tampering is not detected, run proceeds
#    (explicit escape hatch, not a default).
# ----------------------------------------------------------------------


def test_guard_disabled_ignores_tampering(tmp_path):
    cfg = make_config(tmp_path, play_guard_enabled=False)
    ctx = RuntimeCtx(FakeCompiler([FakeCompileResult(0)]), FakeFixer(), _real_clusterer())

    graph = build_graph(cfg, ctx).compile()
    initial = {"spring_repo": str(cfg.spring_repo), "slice_id": "t", "retry_count": 0, "total_llm_calls": 0}
    baseline_path = cfg.migration_dir / "play-baseline.json"
    # No baseline captured at all since play_guard_enabled=False -- and the
    # run must still succeed rather than halting on a missing-baseline error.
    final = graph.invoke(initial, config={"recursion_limit": recursion_limit(cfg)})

    assert final["run_outcome"] == "success"
    assert not baseline_path.exists()


# ----------------------------------------------------------------------
# 4. No play_repo configured -> guard is a no-op (nothing to guard),
#    matching every other play_repo-optional check in this codebase.
# ----------------------------------------------------------------------


def test_no_play_repo_guard_is_noop(tmp_path):
    cfg = AgentConfig(spring_repo=tmp_path)
    cfg.api_key = "test-key"
    ctx = RuntimeCtx(FakeCompiler([FakeCompileResult(0)]), FakeFixer(), _real_clusterer())
    final = run(cfg, ctx)
    assert final["run_outcome"] == "success"
