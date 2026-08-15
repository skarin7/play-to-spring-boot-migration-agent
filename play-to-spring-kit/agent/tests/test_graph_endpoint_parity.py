"""Graph-level T5 endpoint parity wiring (M6 Task 10): degrades to
"not_attempted" with no runner/play_repo/routes configured (never a
run-blocking condition), and a diff finding lands in state["findings"]
without halting the run when a runner IS configured.

The actual dual-boot Play+Spring orchestration is a pluggable seam
(ctx.endpoint_parity_runner) with no default implementation -- see
nodes/endpoint_parity.py's module docstring for why. These tests exercise
the node's own logic via a fake runner, matching how every other injectable
seam in this codebase (boot_runner, test_runner, signature_runner) is tested.
"""

from agent.config import AgentConfig
from agent.graph import RuntimeCtx, build_graph, recursion_limit
from agent.tests.test_graph_flow import FakeCompileResult, FakeCompiler, FakeFixer, _real_clusterer
from agent.tools.maven import BootResult


class FakeBootRunner:
    def __init__(self, results):
        self.results = list(results)

    def __call__(self, config):
        return self.results.pop(0)


def make_config(tmp_path, **overrides) -> AgentConfig:
    play_repo = tmp_path / "play"
    play_repo.mkdir(exist_ok=True)
    spring_repo = tmp_path / "spring"
    spring_repo.mkdir(exist_ok=True)
    (spring_repo / "pom.xml").write_text("<project/>")
    java_dir = spring_repo / "src" / "main" / "java" / "com" / "example"
    java_dir.mkdir(parents=True)
    (java_dir / "Application.java").write_text("class Application {}")
    props_dir = spring_repo / "src" / "main" / "resources"
    props_dir.mkdir(parents=True)
    (props_dir / "application.properties").write_text("")
    (spring_repo / ".migration").mkdir(parents=True, exist_ok=True)
    (spring_repo / ".migration" / "decisions.md").write_text("# Migration Decisions\n")
    cfg = AgentConfig(spring_repo=spring_repo, play_repo=play_repo)
    cfg.api_key = "test-key"
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def run(cfg, ctx):
    graph = build_graph(cfg, ctx).compile()
    initial = {"spring_repo": str(cfg.spring_repo), "retry_count": 0, "total_llm_calls": 0}
    return graph.invoke(initial, config={"recursion_limit": recursion_limit(cfg)})


class RaisingModel:
    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        raise AssertionError("routes agent should not be invoked -- route already mapped")


def _default_ctx_kwargs():
    return dict(
        compiler=FakeCompiler([FakeCompileResult(0)]),
        fixer=FakeFixer(),
        clusterer=_real_clusterer(),
        jar_runner=lambda cfg, prefix: (1, 0),
        boot_runner=FakeBootRunner([BootResult(started=True, log_tail="Started App in 1.0 seconds")]),
        model_override=RaisingModel(),
    )


def _write_mapped_health_controller(spring_repo) -> None:
    java_dir = spring_repo / "src" / "main" / "java" / "controllers"
    java_dir.mkdir(parents=True, exist_ok=True)
    (java_dir / "Health.java").write_text(
        "package controllers;\n"
        "import org.springframework.web.bind.annotation.*;\n"
        "@RestController\n"
        "public class Health {\n"
        '    @GetMapping("/health")\n'
        "    public String check() { return \"ok\"; }\n"
        "}\n"
    )


def test_no_endpoint_parity_runner_configured_not_attempted(tmp_path):
    cfg = make_config(tmp_path)
    ctx = RuntimeCtx(**_default_ctx_kwargs())

    final = run(cfg, ctx)

    assert final["run_outcome"] == "success"
    assert final["endpoint_verification"]["status"] == "not_attempted"
    assert final.get("findings", []) == []


def test_no_play_repo_not_attempted(tmp_path):
    spring_repo = tmp_path / "spring"
    spring_repo.mkdir()
    (spring_repo / "pom.xml").write_text("<project/>")
    java_dir = spring_repo / "src" / "main" / "java" / "com" / "example"
    java_dir.mkdir(parents=True)
    (java_dir / "Application.java").write_text("class Application {}")
    props_dir = spring_repo / "src" / "main" / "resources"
    props_dir.mkdir(parents=True)
    (props_dir / "application.properties").write_text("")
    cfg = AgentConfig(spring_repo=spring_repo)
    cfg.api_key = "test-key"

    def raising_runner(config, routes):
        raise AssertionError("endpoint_parity_runner should not be called without a play_repo")

    ctx = RuntimeCtx(endpoint_parity_runner=raising_runner, **_default_ctx_kwargs())

    final = run(cfg, ctx)

    assert final["endpoint_verification"]["status"] == "not_attempted"


def test_no_routes_not_attempted(tmp_path):
    cfg = make_config(tmp_path)
    calls = []

    def runner(config, routes):
        calls.append(routes)
        return [], [], 0

    ctx = RuntimeCtx(endpoint_parity_runner=runner, **_default_ctx_kwargs())

    final = run(cfg, ctx)

    assert final["endpoint_verification"]["status"] == "not_attempted"
    assert calls == []  # route_map is empty (no conf/routes) -> never even calls the runner


def test_runner_returning_none_is_error_not_halt(tmp_path):
    cfg = make_config(tmp_path)
    (cfg.play_repo / "conf").mkdir()
    (cfg.play_repo / "conf" / "routes").write_text("GET /health controllers.Health.check()\n")
    _write_mapped_health_controller(cfg.spring_repo)

    ctx = RuntimeCtx(endpoint_parity_runner=lambda config, routes: None, **_default_ctx_kwargs())

    final = run(cfg, ctx)

    assert final["run_outcome"] == "success"  # never a halt
    assert final["endpoint_verification"]["status"] == "error"


def test_diff_finding_recorded_never_halts_run(tmp_path):
    cfg = make_config(tmp_path)
    (cfg.play_repo / "conf").mkdir()
    (cfg.play_repo / "conf" / "routes").write_text("GET /health controllers.Health.check()\n")
    _write_mapped_health_controller(cfg.spring_repo)

    diff_entries = [{"path": "/health.status", "kind": "value_changed", "before": "ok", "after": "degraded"}]
    unproved = [{"method": "POST", "path": "/users", "reason": "mutating verb, not probed"}]

    ctx = RuntimeCtx(
        endpoint_parity_runner=lambda config, routes: (diff_entries, unproved, 1), **_default_ctx_kwargs()
    )

    final = run(cfg, ctx)

    assert final["run_outcome"] == "success"
    assert final["endpoint_verification"]["status"] == "differences_found"
    assert final["endpoint_verification"]["probes_compared"] == 1
    assert final["endpoint_verification"]["not_captured_after"] == 1

    findings = final["findings"]
    diff_findings = [f for f in findings if f["category"] == "endpoint_diff"]
    unproved_findings = [f for f in findings if f["category"] == "endpoint_unproved"]
    assert len(diff_findings) == 1
    assert diff_findings[0]["tier"] == "T5"
    assert diff_findings[0]["severity"] == "major"
    assert len(unproved_findings) == 1
    assert unproved_findings[0]["severity"] == "minor"
    assert unproved_findings[0]["route"] == "POST /users"


def test_no_diff_is_passed_status(tmp_path):
    cfg = make_config(tmp_path)
    (cfg.play_repo / "conf").mkdir()
    (cfg.play_repo / "conf" / "routes").write_text("GET /health controllers.Health.check()\n")
    _write_mapped_health_controller(cfg.spring_repo)

    ctx = RuntimeCtx(endpoint_parity_runner=lambda config, routes: ([], [], 1), **_default_ctx_kwargs())

    final = run(cfg, ctx)

    assert final["endpoint_verification"]["status"] == "passed"
    assert final.get("findings", []) == []
