"""Graph-level bootstrap routing: skip-when-already-set-up, success, exhausted."""

from langchain_core.messages import AIMessage

from agent.config import AgentConfig
from agent.graph import RuntimeCtx, build_graph, recursion_limit
from agent.tests.test_bootstrap_agent import FakeToolModel
from agent.tests.test_graph_flow import FakeCompileResult, FakeCompiler, FakeFixer, _real_clusterer


class FakeSetupOps:
    def __init__(self):
        self.calls: list[str] = []

    def ensure_jar(self, config):
        self.calls.append("ensure_jar")
        return True, "ok"

    def install(self, config):
        self.calls.append("install")
        return True, "ok"

    def export_conf(self, config):
        self.calls.append("export_conf")
        return True, "ok"


def make_config(tmp_path, **overrides) -> AgentConfig:
    play_repo = tmp_path / "play"
    play_repo.mkdir(exist_ok=True)
    spring_repo = tmp_path / "spring"
    spring_repo.mkdir(exist_ok=True)
    cfg = AgentConfig(spring_repo=spring_repo, play_repo=play_repo)
    cfg.api_key = "test-key"
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def run(cfg, ctx):
    graph = build_graph(cfg, ctx).compile()
    initial = {"spring_repo": str(cfg.spring_repo), "retry_count": 0, "total_llm_calls": 0}
    return graph.invoke(initial, config={"recursion_limit": recursion_limit(cfg)})


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


def test_skips_bootstrap_when_files_already_present(tmp_path):
    cfg = make_config(tmp_path)
    _make_bootstrapped(cfg.spring_repo)
    setup_ops = FakeSetupOps()
    compiler = FakeCompiler([FakeCompileResult(0)])
    ctx = RuntimeCtx(compiler, FakeFixer(), _real_clusterer(), setup_ops=setup_ops)
    final = run(cfg, ctx)
    assert final["bootstrap_decision"] == "skip"
    assert final.get("bootstrap_attempts", 0) == 0
    assert final["run_outcome"] == "success"
    assert setup_ops.calls == ["ensure_jar", "install", "export_conf"]


def test_bootstrap_agent_succeeds_on_first_attempt(tmp_path):
    cfg = make_config(tmp_path)
    model = FakeToolModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "write_file", "args": {"path": "pom.xml", "content": "<project/>"}, "id": "1"},
                    {
                        "name": "write_file",
                        "args": {"path": "src/main/java/Application.java", "content": "class Application {}"},
                        "id": "2",
                    },
                    {
                        "name": "write_file",
                        "args": {"path": "src/main/resources/application.properties", "content": ""},
                        "id": "3",
                    },
                ],
            ),
            AIMessage(content="done"),
        ]
    )
    architect_model = FakeToolModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {"path": ".migration/decisions.md", "content": "# Migration Decisions\n"},
                        "id": "1",
                    }
                ],
            ),
            AIMessage(content="done"),
        ]
    )
    compiler = FakeCompiler([FakeCompileResult(0)])
    ctx = RuntimeCtx(
        compiler,
        FakeFixer(),
        _real_clusterer(),
        setup_ops=FakeSetupOps(),
        bootstrap_model_override=model,
        architect_model_override=architect_model,
    )
    final = run(cfg, ctx)
    assert final["bootstrap_attempts"] == 1
    assert final["bootstrap_decision"] == "skip"
    assert final["run_outcome"] == "success"
    assert (cfg.spring_repo / "pom.xml").is_file()


def test_bootstrap_exhausted_after_max_attempts_exits_3(tmp_path):
    cfg = make_config(tmp_path, max_bootstrap_attempts=2)
    # Never writes any files -> stays un-bootstrapped every attempt.
    model = FakeToolModel([AIMessage(content="attempt 1"), AIMessage(content="attempt 2")])
    compiler = FakeCompiler([])
    ctx = RuntimeCtx(
        compiler, FakeFixer(), _real_clusterer(), setup_ops=FakeSetupOps(), bootstrap_model_override=model
    )
    final = run(cfg, ctx)
    assert final["bootstrap_attempts"] == 2
    assert final["bootstrap_decision"] == "exhausted"
    assert final["run_outcome"] == "init_failed"
    assert final["run_exit_code"] == 3
    assert compiler.calls == 0  # never reached the slice pipeline
