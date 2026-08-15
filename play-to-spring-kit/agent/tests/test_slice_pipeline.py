"""Multi-slice routing tests: slice_router/transform/slice_finalize/verify aggregation.

Seeds ``migration_units`` directly in the initial state so inventory_node's
discovery is a no-op (legacy parity: it only (re)discovers when units are
empty) and the fake units/compiler fully control the scenario.
"""

from agent.config import AgentConfig
from agent.graph import RuntimeCtx, build_graph, recursion_limit
from agent.inventory import default_unit_entry
from agent.tests.test_graph_flow import FakeCompileResult, FakeCompiler, FakeFixer, _real_clusterer


class FakeJarRunner:
    def __init__(self):
        self.calls: list[str] = []

    def __call__(self, config, path_prefix):
        self.calls.append(path_prefix)
        return (0, 0)


def make_config(tmp_path, **overrides) -> AgentConfig:
    cfg = AgentConfig(spring_repo=tmp_path)
    cfg.api_key = "test-key"
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def run(cfg, ctx, units):
    graph = build_graph(cfg, ctx).compile()
    initial = {
        "spring_repo": str(cfg.spring_repo),
        "retry_count": 0,
        "total_llm_calls": 0,
        "migration_units": units,
    }
    return graph.invoke(initial, config={"recursion_limit": recursion_limit(cfg)})


def test_two_slices_both_succeed(tmp_path):
    cfg = make_config(tmp_path)
    units = [default_unit_entry("a", "a", 0), default_unit_entry("b", "b", 0)]
    jar = FakeJarRunner()
    # one compile per slice + one cross-module verify compile at the end
    compiler = FakeCompiler([FakeCompileResult(0), FakeCompileResult(0), FakeCompileResult(0)])
    ctx = RuntimeCtx(compiler, FakeFixer(), _real_clusterer(), jar_runner=jar)
    final = run(cfg, ctx, units)
    assert final["run_outcome"] == "success"
    assert final["run_exit_code"] == 0
    statuses = {u["id"]: u["status"] for u in final["migration_units"]}
    assert statuses == {"a": "done", "b": "done"}
    assert jar.calls == ["a", "b"]


def test_budget_exhausted_aborts_before_second_slice(tmp_path):
    cfg = make_config(tmp_path, max_total_llm_calls=0)
    units = [default_unit_entry("a", "a", 0), default_unit_entry("b", "b", 0)]
    jar = FakeJarRunner()
    ERR = {"file": "A.java", "line": 3, "message": "cannot find symbol X"}
    compiler = FakeCompiler([FakeCompileResult(1, [ERR])])
    ctx = RuntimeCtx(compiler, FakeFixer([0]), _real_clusterer(), jar_runner=jar)
    final = run(cfg, ctx, units)
    assert final["run_outcome"] == "budget_exhausted"
    assert final["run_exit_code"] == 4
    # second slice was never reached: transform never ran, unit still pending.
    assert jar.calls == ["a"]
    by_id = {u["id"]: u for u in final["migration_units"]}
    assert by_id["b"]["status"] == "pending"


class FakeInventoryRunner:
    def __init__(self, report):
        self.report = report
        self.calls = 0

    def __call__(self, config):
        self.calls += 1
        return self.report


def test_inventory_node_logs_paradigm_and_unknown_gaps_before_transform(tmp_path, caplog):
    play_repo = tmp_path / "play"
    (play_repo / "app").mkdir(parents=True)
    spring_repo = tmp_path / "spring"
    (spring_repo / "src" / "main" / "java").mkdir(parents=True)
    (spring_repo / "src" / "main" / "java" / "Application.java").write_text("class Application {}")
    (spring_repo / "src" / "main" / "resources").mkdir(parents=True)
    (spring_repo / "src" / "main" / "resources" / "application.properties").write_text("")
    (spring_repo / "pom.xml").write_text("<project></project>")
    (spring_repo / ".migration").mkdir(parents=True, exist_ok=True)
    (spring_repo / ".migration" / "decisions.md").write_text("# Migration Decisions\n")
    cfg = AgentConfig(spring_repo=spring_repo, play_repo=play_repo)
    cfg.api_key = "test-key"
    jar = FakeJarRunner()
    fake_report = {
        "coveragePercent": 50.0,
        "knownCount": 1,
        "unknownCount": 1,
        "paradigmCount": 1,
        "touchpoints": [
            {"construct": "akka.actor.UntypedActor", "location": "WorkerActor.java:1", "classification": "PARADIGM"},
            {"construct": "play.data.Form", "location": "SignupController.java:3", "classification": "UNKNOWN"},
        ],
    }
    inv_runner = FakeInventoryRunner(fake_report)
    compiler = FakeCompiler([FakeCompileResult(0)])
    ctx = RuntimeCtx(compiler, FakeFixer(), _real_clusterer(), jar_runner=jar, inventory_runner=inv_runner)
    graph = build_graph(cfg, ctx).compile()
    initial = {"spring_repo": str(cfg.spring_repo), "retry_count": 0, "total_llm_calls": 0}
    with caplog.at_level("WARNING", logger="agent.nodes.slice_pipeline"):
        graph.invoke(initial, config={"recursion_limit": recursion_limit(cfg)})
    messages = " ".join(r.message for r in caplog.records)
    assert "50.0" in messages
    assert "akka.actor.UntypedActor" in messages
    assert "play.data.Form" in messages


def test_inventory_node_calls_inventory_runner_and_stores_report(tmp_path):
    play_repo = tmp_path / "play"
    (play_repo / "app").mkdir(parents=True)
    spring_repo = tmp_path / "spring"
    # Pre-scaffold so bootstrap_check_node skips straight to inventory (no real LLM call).
    (spring_repo / "src" / "main" / "java").mkdir(parents=True)
    (spring_repo / "src" / "main" / "java" / "Application.java").write_text("class Application {}")
    (spring_repo / "src" / "main" / "resources").mkdir(parents=True)
    (spring_repo / "src" / "main" / "resources" / "application.properties").write_text("")
    (spring_repo / "pom.xml").write_text("<project></project>")
    (spring_repo / ".migration").mkdir(parents=True, exist_ok=True)
    (spring_repo / ".migration" / "decisions.md").write_text("# Migration Decisions\n")
    cfg = AgentConfig(spring_repo=spring_repo, play_repo=play_repo)
    cfg.api_key = "test-key"
    jar = FakeJarRunner()
    fake_report = {"coveragePercent": 50.0, "knownCount": 1, "unknownCount": 0, "paradigmCount": 1, "touchpoints": []}
    inv_runner = FakeInventoryRunner(fake_report)
    compiler = FakeCompiler([FakeCompileResult(0)])
    ctx = RuntimeCtx(compiler, FakeFixer(), _real_clusterer(), jar_runner=jar, inventory_runner=inv_runner)
    graph = build_graph(cfg, ctx).compile()
    initial = {"spring_repo": str(cfg.spring_repo), "retry_count": 0, "total_llm_calls": 0}
    final = graph.invoke(initial, config={"recursion_limit": recursion_limit(cfg)})
    assert final["play_surface_inventory"] == fake_report
    assert inv_runner.calls == 1


def test_one_slice_fails_other_succeeds_yields_slice_failure_exit_code(tmp_path):
    cfg = make_config(tmp_path, max_retries_per_layer=0)
    units = [default_unit_entry("a", "a", 0), default_unit_entry("b", "b", 0)]
    jar = FakeJarRunner()
    ERR = {"file": "B.java", "line": 3, "message": "cannot find symbol Y"}
    # a: clean compile. b: errors every round, det_fix never makes progress ->
    # retries_exhausted immediately (max_retries_per_layer=0). Final verify
    # compile falls back to the FakeCompiler default (clean, no errors).
    compiler = FakeCompiler([FakeCompileResult(0), FakeCompileResult(1, [ERR])])
    ctx = RuntimeCtx(compiler, FakeFixer([0]), _real_clusterer(), jar_runner=jar)
    final = run(cfg, ctx, units)
    assert final["run_outcome"] == "slice_failures"
    assert final["run_exit_code"] == 5
    by_id = {u["id"]: u for u in final["migration_units"]}
    assert by_id["a"]["status"] == "done"
    assert by_id["b"]["status"] == "failed"
    assert by_id["b"]["failure_reason"] == "max_retries"
    assert jar.calls == ["a", "b"]


def test_transform_reports_migrate_app_errors_as_tool_error_gap(tmp_path):
    """M6 Task 8: migrate-app itself reporting errors (m_err > 0) is a
    tool_error signal -- the toolkit JAR hit something it couldn't
    transform, which is exactly the class of blind spot the gaps loop
    exists to aggregate across installs."""
    from agent.tools.gaps import read_gaps

    cfg = make_config(tmp_path)
    units = [default_unit_entry("a", "a", 0)]

    class ErroringJarRunner:
        def __call__(self, config, path_prefix):
            return (2, 3)  # 2 files migrated, 3 errors reported by the JAR

    compiler = FakeCompiler([FakeCompileResult(0)])
    ctx = RuntimeCtx(compiler, FakeFixer(), _real_clusterer(), jar_runner=ErroringJarRunner())
    run(cfg, ctx, units)

    gap_entries = read_gaps(cfg.spring_repo)
    tool_error_gaps = [g for g in gap_entries if g["kind"] == "tool_error"]
    assert len(tool_error_gaps) == 1
    assert "3 error" in tool_error_gaps[0]["what_i_did"]


def test_transform_no_gap_when_migrate_app_reports_zero_errors(tmp_path):
    from agent.tools.gaps import read_gaps

    cfg = make_config(tmp_path)
    units = [default_unit_entry("a", "a", 0)]
    jar = FakeJarRunner()  # returns (0, 0)
    compiler = FakeCompiler([FakeCompileResult(0)])
    ctx = RuntimeCtx(compiler, FakeFixer(), _real_clusterer(), jar_runner=jar)
    run(cfg, ctx, units)

    assert read_gaps(cfg.spring_repo) == []
