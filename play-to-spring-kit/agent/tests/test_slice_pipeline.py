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
