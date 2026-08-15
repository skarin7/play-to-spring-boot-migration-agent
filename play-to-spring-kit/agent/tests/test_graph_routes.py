"""Graph-level routes-phase routing (M4): no-op, already-mapped, agent-mapped,
attempts-exhausted, routes-fix compile cycle, and budget-exhausted abort.

Follows test_graph_bootstrap.py's style: FakeCompiler/FakeFixer/FakeToolModel +
RuntimeCtx(...) + build_graph(cfg, ctx).compile() + graph.invoke(...).
"""

import json

from langchain_core.messages import AIMessage

from agent.config import AgentConfig
from agent.graph import RuntimeCtx, build_graph, recursion_limit
from agent.tests.test_graph_flow import (
    FakeCompileResult,
    FakeCompiler,
    FakeFixer,
    FakeToolModel,
    _real_clusterer,
)

ROUTES_TXT = "GET     /users                      controllers.UserController.list()\n"

UNMAPPED_JAVA = (
    "package controllers;\n\n"
    "import org.springframework.web.bind.annotation.*;\n\n"
    "@RestController\n"
    "public class UserController {\n"
    "    public ResponseEntity<List<User>> list() {\n"
    "        return null;\n"
    "    }\n"
    "}\n"
)

MAPPED_JAVA = (
    "package controllers;\n\n"
    "import org.springframework.web.bind.annotation.*;\n\n"
    "@RestController\n"
    "public class UserController {\n"
    '    @GetMapping("/users")\n'
    "    public ResponseEntity<List<User>> list() {\n"
    "        return null;\n"
    "    }\n"
    "}\n"
)

ERR = {"file": "A.java", "line": 3, "message": "cannot find symbol X"}


def _make_bootstrapped(spring_repo) -> None:
    (spring_repo / "pom.xml").write_text("<project/>")
    java_dir = spring_repo / "src" / "main" / "java" / "com" / "example"
    java_dir.mkdir(parents=True)
    (java_dir / "Application.java").write_text("class Application {}")
    props_dir = spring_repo / "src" / "main" / "resources"
    props_dir.mkdir(parents=True)
    (props_dir / "application.properties").write_text("")


def _write_controller(spring_repo, content: str) -> None:
    java_dir = spring_repo / "src" / "main" / "java" / "controllers"
    java_dir.mkdir(parents=True, exist_ok=True)
    (java_dir / "UserController.java").write_text(content, encoding="utf-8")


def make_config(tmp_path, routes_text: str | None = None, **overrides) -> AgentConfig:
    play_repo = tmp_path / "play"
    play_repo.mkdir(exist_ok=True)
    if routes_text is not None:
        conf_dir = play_repo / "conf"
        conf_dir.mkdir(exist_ok=True)
        (conf_dir / "routes").write_text(routes_text, encoding="utf-8")
    spring_repo = tmp_path / "spring"
    spring_repo.mkdir(exist_ok=True)
    _make_bootstrapped(spring_repo)
    cfg = AgentConfig(spring_repo=spring_repo, play_repo=play_repo)
    cfg.api_key = "test-key"
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def run(cfg, ctx):
    graph = build_graph(cfg, ctx).compile()
    initial = {"spring_repo": str(cfg.spring_repo), "retry_count": 0, "total_llm_calls": 0}
    return graph.invoke(initial, config={"recursion_limit": recursion_limit(cfg)})


def _map_route_responses():
    """Two AIMessages: one tool call that adds the @GetMapping, one that ends the loop."""
    return [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "str_replace",
                    "args": {
                        "path": "src/main/java/controllers/UserController.java",
                        "old": "    public ResponseEntity<List<User>> list() {",
                        "new": '    @GetMapping("/users")\n    public ResponseEntity<List<User>> list() {',
                    },
                    "id": "1",
                }
            ],
        ),
        AIMessage(content="done"),
    ]


class RaisingModel:
    """Fails the test loudly if the routes/compile-fix agent is ever invoked."""

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        raise AssertionError("LLM should not have been invoked in this scenario")


# ----------------------------------------------------------------------
# 1. No play_repo / no conf/routes: no-op, straight through, never invoked.
# ----------------------------------------------------------------------


def test_no_play_repo_routes_phase_is_noop(tmp_path):
    cfg = AgentConfig(spring_repo=tmp_path)
    cfg.api_key = "test-key"
    compiler = FakeCompiler([FakeCompileResult(0)])
    ctx = RuntimeCtx(compiler, FakeFixer(), _real_clusterer(), model_override=RaisingModel())
    final = run(cfg, ctx)
    assert final["run_outcome"] == "success"
    assert final.get("routes_attempts", 0) == 0
    route_map_path = cfg.migration_dir / "route-map.json"
    assert route_map_path.is_file()
    assert json.loads(route_map_path.read_text())["unmapped"] == []


def test_play_repo_without_conf_routes_is_noop(tmp_path):
    cfg = make_config(tmp_path, routes_text=None)
    compiler = FakeCompiler([FakeCompileResult(0)])
    ctx = RuntimeCtx(compiler, FakeFixer(), _real_clusterer(), model_override=RaisingModel())
    final = run(cfg, ctx)
    assert final["run_outcome"] == "success"
    assert final.get("routes_attempts", 0) == 0


# ----------------------------------------------------------------------
# 2. All routes already mapped: agent never invoked.
# ----------------------------------------------------------------------


def test_all_routes_already_mapped_skips_agent(tmp_path):
    cfg = make_config(tmp_path, routes_text=ROUTES_TXT)
    _write_controller(cfg.spring_repo, MAPPED_JAVA)
    compiler = FakeCompiler([FakeCompileResult(0), FakeCompileResult(0)])
    ctx = RuntimeCtx(compiler, FakeFixer(), _real_clusterer(), model_override=RaisingModel())
    final = run(cfg, ctx)
    assert final["run_outcome"] == "success"
    assert final["total_llm_calls"] == 0
    route_map = json.loads((cfg.migration_dir / "route-map.json").read_text())
    assert route_map["unmapped"] == []
    assert len(route_map["mapped"]) == 1


# ----------------------------------------------------------------------
# 3. Unmapped routes present: agent invoked once, maps it, run succeeds.
# ----------------------------------------------------------------------


def test_unmapped_route_gets_mapped_by_agent(tmp_path):
    cfg = make_config(tmp_path, routes_text=ROUTES_TXT)
    _write_controller(cfg.spring_repo, UNMAPPED_JAVA)
    compiler = FakeCompiler([FakeCompileResult(0), FakeCompileResult(0)])
    model = FakeToolModel(_map_route_responses())
    ctx = RuntimeCtx(compiler, FakeFixer(), _real_clusterer(), model_override=model)
    final = run(cfg, ctx)
    assert final["run_outcome"] == "success"
    assert final["routes_attempts"] == 1
    assert final["total_llm_calls"] == 1
    java_text = (cfg.spring_repo / "src/main/java/controllers/UserController.java").read_text()
    assert '@GetMapping("/users")' in java_text
    route_map = json.loads((cfg.migration_dir / "route-map.json").read_text())
    assert route_map["unmapped"] == []


# ----------------------------------------------------------------------
# 4. Unmapped routes remain after max_routes_attempts: non-blocking.
# ----------------------------------------------------------------------


def test_unmapped_routes_remain_after_max_attempts_is_non_blocking(tmp_path):
    cfg = make_config(tmp_path, routes_text=ROUTES_TXT, max_routes_attempts=1)
    _write_controller(cfg.spring_repo, UNMAPPED_JAVA)
    compiler = FakeCompiler([FakeCompileResult(0), FakeCompileResult(0)])
    # Agent "tries" but never actually adds the annotation -> stays unmapped.
    model = FakeToolModel([AIMessage(content="tried but gave up")])
    ctx = RuntimeCtx(compiler, FakeFixer(), _real_clusterer(), model_override=model)
    final = run(cfg, ctx)
    assert final["run_outcome"] == "success"
    assert final["routes_attempts"] == 1
    route_map = json.loads((cfg.migration_dir / "route-map.json").read_text())
    assert len(route_map["unmapped"]) == 1


# ----------------------------------------------------------------------
# 4b. Global LLM budget (max_total_llm_calls), not just max_routes_attempts,
#     is a hard stop for routes_node too.
# ----------------------------------------------------------------------


def test_routes_node_respects_global_budget_zero_never_invokes_agent(tmp_path):
    cfg = make_config(tmp_path, routes_text=ROUTES_TXT, max_total_llm_calls=0)
    _write_controller(cfg.spring_repo, UNMAPPED_JAVA)
    compiler = FakeCompiler([FakeCompileResult(0)])
    ctx = RuntimeCtx(compiler, FakeFixer(), _real_clusterer(), model_override=RaisingModel())
    final = run(cfg, ctx)
    assert final["run_outcome"] == "budget_exhausted"
    assert final["run_exit_code"] == 4
    assert final["total_llm_calls"] == 0
    route_map = json.loads((cfg.migration_dir / "route-map.json").read_text())
    assert len(route_map["unmapped"]) == 1


def test_routes_node_stops_after_budget_exhausted_mid_loop(tmp_path):
    """Regression: with max_total_llm_calls=1 and a model that never actually
    maps the route, routes_node must make exactly 1 LLM call (not 2) and halt
    with budget_exhausted, rather than looping past the global budget on the
    strength of max_routes_attempts alone."""
    cfg = make_config(tmp_path, routes_text=ROUTES_TXT, max_total_llm_calls=1, max_routes_attempts=5)
    _write_controller(cfg.spring_repo, UNMAPPED_JAVA)
    compiler = FakeCompiler([FakeCompileResult(0)])
    # Only one response queued: a second model.invoke() (i.e. a second LLM
    # call past the budget) would raise IndexError and fail the test.
    model = FakeToolModel([AIMessage(content="tried but gave up")])
    ctx = RuntimeCtx(compiler, FakeFixer(), _real_clusterer(), model_override=model)
    final = run(cfg, ctx)
    assert final["run_outcome"] == "budget_exhausted"
    assert final["run_exit_code"] == 4
    assert final["total_llm_calls"] == 1
    assert final["routes_attempts"] == 1


# ----------------------------------------------------------------------
# 5. Routes-fix compile cycle: agent maps route, forced compile error routes
#    through det_fix/cluster/guard/agent, terminates via after_routes_fix.
# ----------------------------------------------------------------------


def test_routes_fix_compile_cycle_recovers_via_agent(tmp_path):
    cfg = make_config(tmp_path, routes_text=ROUTES_TXT)
    _write_controller(cfg.spring_repo, UNMAPPED_JAVA)
    # 1: slice-pipeline compile (clean). 2: routes_fix_prep compile (error).
    # 3: recompile after the compile-fix agent round (clean).
    compiler = FakeCompiler(
        [FakeCompileResult(0), FakeCompileResult(1, [ERR]), FakeCompileResult(0)]
    )
    # Responses consumed in order: routes-agent round (2 msgs), then the
    # compile-fix agent round (1 msg, no tool calls -> ends immediately).
    model = FakeToolModel(_map_route_responses() + [AIMessage(content="fixed it")])
    ctx = RuntimeCtx(compiler, FakeFixer([0]), _real_clusterer(), model_override=model)
    final = run(cfg, ctx)
    assert final["run_outcome"] == "success"
    assert final["outcome"] == "success"
    assert final["total_llm_calls"] == 2  # 1 routes round + 1 compile-fix round
    assert final["phase"] == "slice"  # after_routes_fix reset it back


# ----------------------------------------------------------------------
# 6. Budget exhausted during the routes-fix compile cycle aborts the run.
# ----------------------------------------------------------------------


def test_budget_exhausted_during_routes_fix_aborts_run(tmp_path):
    cfg = make_config(tmp_path, routes_text=ROUTES_TXT, max_total_llm_calls=1)
    _write_controller(cfg.spring_repo, UNMAPPED_JAVA)
    # 1: slice-pipeline compile (clean). 2: routes_fix_prep compile (error) ->
    # det_fix makes no progress -> guard sees total_llm_calls(1) >= budget(1)
    # -> halt(budget_exhausted) -> after_routes_fix aborts, never reaches verify.
    compiler = FakeCompiler([FakeCompileResult(0), FakeCompileResult(1, [ERR])])
    model = FakeToolModel(_map_route_responses())
    ctx = RuntimeCtx(compiler, FakeFixer([0]), _real_clusterer(), model_override=model)
    final = run(cfg, ctx)
    assert final["run_outcome"] == "budget_exhausted"
    assert final["run_exit_code"] == 4
