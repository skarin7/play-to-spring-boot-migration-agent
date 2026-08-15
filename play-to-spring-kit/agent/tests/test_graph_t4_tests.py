"""Graph-level T4 (mvn test) wiring in verify_node (M6 Task 10): a test
failure is a finding, never a halt; skipped entirely when the final compile
is still broken; off via config.run_tests; and no_test_runner_configured is
a silent no-op matching every other optional RuntimeCtx component."""

from agent.config import AgentConfig
from agent.graph import RuntimeCtx, build_graph, recursion_limit
from agent.inventory import default_unit_entry
from agent.tests.test_graph_flow import FakeCompileResult, FakeCompiler, FakeFixer, _real_clusterer
from agent.tools.maven import TestResult


def make_config(tmp_path, **overrides) -> AgentConfig:
    cfg = AgentConfig(spring_repo=tmp_path)
    cfg.api_key = "test-key"
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def run(cfg, ctx, units=None):
    graph = build_graph(cfg, ctx).compile()
    initial = {
        "spring_repo": str(cfg.spring_repo),
        "retry_count": 0,
        "total_llm_calls": 0,
    }
    if units is not None:
        initial["migration_units"] = units
    return graph.invoke(initial, config={"recursion_limit": recursion_limit(cfg)})


class FakeTestRunner:
    def __init__(self, result: TestResult):
        self.result = result
        self.calls = 0

    def __call__(self, config):
        self.calls += 1
        return self.result


def test_all_tests_pass_no_finding(tmp_path):
    cfg = make_config(tmp_path)
    units = [default_unit_entry("a", "a", 0)]
    compiler = FakeCompiler([FakeCompileResult(0), FakeCompileResult(0)])
    test_runner = FakeTestRunner(TestResult(returncode=0, log_tail="", passed=5, failed=0, errors=0, skipped=0))
    ctx = RuntimeCtx(compiler, FakeFixer(), _real_clusterer(), jar_runner=lambda c, p: (0, 0), test_runner=test_runner)

    final = run(cfg, ctx, units)

    assert final["run_outcome"] == "success"
    assert test_runner.calls == 1
    assert final["test_result"]["all_passed"] is True
    assert final.get("findings", []) == []


def test_test_failure_is_a_finding_not_a_halt(tmp_path):
    cfg = make_config(tmp_path)
    units = [default_unit_entry("a", "a", 0)]
    compiler = FakeCompiler([FakeCompileResult(0), FakeCompileResult(0)])
    test_runner = FakeTestRunner(
        TestResult(returncode=1, log_tail="failure log", passed=8, failed=2, errors=0, skipped=0)
    )
    ctx = RuntimeCtx(compiler, FakeFixer(), _real_clusterer(), jar_runner=lambda c, p: (0, 0), test_runner=test_runner)

    final = run(cfg, ctx, units)

    assert final["run_outcome"] == "success"  # never a halt
    findings = final.get("findings", [])
    assert len(findings) == 1
    assert findings[0]["tier"] == "T4"
    assert findings[0]["severity"] == "major"
    assert findings[0]["category"] == "test_failure"
    assert findings[0]["failed"] == 2


def test_skipped_when_final_compile_still_broken(tmp_path):
    """mvn test on a red build produces noise, not signal -- must not even
    be attempted."""
    cfg = make_config(tmp_path, max_retries_per_layer=0)
    units = [default_unit_entry("a", "a", 0)]
    ERR = {"file": "A.java", "line": 1, "message": "cannot find symbol"}
    # slice a fails every round -> retries_exhausted; final verify compile
    # (FakeCompiler default when out of scripted results) also still fails.
    compiler = FakeCompiler([FakeCompileResult(1, [ERR]), FakeCompileResult(1, [ERR])])
    test_runner = FakeTestRunner(TestResult(returncode=0, log_tail="", passed=1))
    ctx = RuntimeCtx(compiler, FakeFixer([0]), _real_clusterer(), jar_runner=lambda c, p: (0, 0), test_runner=test_runner)

    run(cfg, ctx, units)

    assert test_runner.calls == 0


def test_run_tests_disabled_skips_entirely(tmp_path):
    cfg = make_config(tmp_path, run_tests=False)
    units = [default_unit_entry("a", "a", 0)]
    compiler = FakeCompiler([FakeCompileResult(0), FakeCompileResult(0)])
    test_runner = FakeTestRunner(TestResult(returncode=1, log_tail="", failed=99))
    ctx = RuntimeCtx(compiler, FakeFixer(), _real_clusterer(), jar_runner=lambda c, p: (0, 0), test_runner=test_runner)

    final = run(cfg, ctx, units)

    assert test_runner.calls == 0
    assert final.get("findings", []) == []


def test_no_test_runner_configured_is_noop(tmp_path):
    cfg = make_config(tmp_path)
    units = [default_unit_entry("a", "a", 0)]
    compiler = FakeCompiler([FakeCompileResult(0), FakeCompileResult(0)])
    ctx = RuntimeCtx(compiler, FakeFixer(), _real_clusterer(), jar_runner=lambda c, p: (0, 0))

    final = run(cfg, ctx, units)

    assert final["run_outcome"] == "success"
    assert final.get("findings", []) == []
