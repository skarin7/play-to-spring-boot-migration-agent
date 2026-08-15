"""Graph-level architect phase routing (M6 Task 6): no-op when no play_repo,
skip when decisions.md already present (resumed run), agent writes it fresh,
exhausted after max attempts, and the interactive approval gate
(headless-never-interrupts, approve/revise/garbage-resume).

Follows test_graph_bootstrap.py's style: FakeCompiler/FakeFixer/FakeToolModel
+ RuntimeCtx(...) + build_graph(cfg, ctx).compile() + graph.invoke(...).
"""

from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agent.agents.architect import DECISIONS_RELATIVE_PATH
from agent.config import AgentConfig
from agent.graph import RuntimeCtx, build_graph, recursion_limit
from agent.state import EXIT_INIT_NOT_DONE, EXIT_OK
from agent.tests.test_bootstrap_agent import FakeToolModel
from agent.tests.test_graph_flow import FakeCompileResult, FakeCompiler, FakeFixer, _real_clusterer


class RaisingModel:
    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        raise AssertionError("architect agent should not have been invoked in this scenario")


def _make_bootstrapped(spring_repo) -> None:
    (spring_repo / "pom.xml").write_text("<project/>")
    java_dir = spring_repo / "src" / "main" / "java" / "com" / "example"
    java_dir.mkdir(parents=True)
    (java_dir / "Application.java").write_text("class Application {}")
    props_dir = spring_repo / "src" / "main" / "resources"
    props_dir.mkdir(parents=True)
    (props_dir / "application.properties").write_text("")


def make_config(tmp_path, **overrides) -> AgentConfig:
    play_repo = tmp_path / "play"
    play_repo.mkdir(exist_ok=True)
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


def _write_decisions_response():
    return [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "write_file",
                    "args": {"path": DECISIONS_RELATIVE_PATH, "content": "# Migration Decisions\n"},
                    "id": "1",
                }
            ],
        ),
        AIMessage(content="done"),
    ]


# ----------------------------------------------------------------------
# 1. No play_repo -> architect is a no-op, matching every other
#    play_repo-optional phase.
# ----------------------------------------------------------------------


def test_no_play_repo_architect_is_noop(tmp_path):
    spring_repo = tmp_path / "spring"
    spring_repo.mkdir()
    _make_bootstrapped(spring_repo)
    cfg = AgentConfig(spring_repo=spring_repo)
    cfg.api_key = "test-key"
    ctx = RuntimeCtx(
        FakeCompiler([FakeCompileResult(0)]), FakeFixer(), _real_clusterer(), architect_model_override=RaisingModel()
    )
    final = run(cfg, ctx)
    assert final["run_outcome"] == "success"
    assert final.get("architect_decision") == "skip"


# ----------------------------------------------------------------------
# 2. decisions.md already present (resumed run) -> skip straight past the
#    agent AND the approval gate.
# ----------------------------------------------------------------------


def test_decisions_already_present_skips_agent_and_gate(tmp_path):
    cfg = make_config(tmp_path, headless=False)  # headless=False proves the gate is bypassed, not just auto-approved
    (cfg.spring_repo / ".migration").mkdir(parents=True, exist_ok=True)
    (cfg.spring_repo / DECISIONS_RELATIVE_PATH).write_text("# pre-existing\n")
    ctx = RuntimeCtx(
        FakeCompiler([FakeCompileResult(0)]), FakeFixer(), _real_clusterer(), architect_model_override=RaisingModel()
    )
    final = run(cfg, ctx)
    assert final["run_outcome"] == "success"
    assert final.get("architect_decision") == "skip"
    assert "__interrupt__" not in final


# ----------------------------------------------------------------------
# 3. Agent writes decisions.md on first attempt -> headless auto-approves,
#    run proceeds.
# ----------------------------------------------------------------------


def test_architect_agent_writes_decisions_then_auto_approved_headless(tmp_path):
    cfg = make_config(tmp_path)
    model = FakeToolModel(_write_decisions_response())
    ctx = RuntimeCtx(
        FakeCompiler([FakeCompileResult(0)]), FakeFixer(), _real_clusterer(), architect_model_override=model
    )
    final = run(cfg, ctx)
    assert final["run_outcome"] == "success"
    assert final["architect_attempts"] == 1
    assert final["architect_decision"] == "approved"
    assert (cfg.spring_repo / DECISIONS_RELATIVE_PATH).is_file()


# ----------------------------------------------------------------------
# 4. Agent never writes decisions.md -> exhausted after max attempts,
#    run halts with init_failed (same bucket as bootstrap exhaustion).
# ----------------------------------------------------------------------


def test_architect_exhausted_after_max_attempts(tmp_path):
    cfg = make_config(tmp_path, max_architect_attempts=2)
    model = FakeToolModel([AIMessage(content="attempt 1"), AIMessage(content="attempt 2")])
    ctx = RuntimeCtx(
        FakeCompiler([]), FakeFixer(), _real_clusterer(), architect_model_override=model
    )
    final = run(cfg, ctx)
    assert final["run_outcome"] == "init_failed"
    assert final["run_exit_code"] == EXIT_INIT_NOT_DONE
    assert final["architect_attempts"] == 2


# ----------------------------------------------------------------------
# 5. Interactive gate: pauses via interrupt(), "approve" proceeds,
#    anything else (including garbage) defaults to "revise" and halts.
# ----------------------------------------------------------------------


def test_interactive_gate_approve_proceeds(tmp_path):
    cfg = make_config(tmp_path, headless=False)
    model = FakeToolModel(_write_decisions_response())
    ctx = RuntimeCtx(
        FakeCompiler([FakeCompileResult(0)]), FakeFixer(), _real_clusterer(), architect_model_override=model
    )
    graph = build_graph(cfg, ctx).compile(checkpointer=InMemorySaver())
    run_config = {"configurable": {"thread_id": "arch-approve"}, "recursion_limit": recursion_limit(cfg)}
    initial = {"spring_repo": str(cfg.spring_repo), "retry_count": 0, "total_llm_calls": 0}

    final = graph.invoke(initial, config=run_config)
    assert "__interrupt__" in final

    final = graph.invoke(Command(resume="approve"), config=run_config)
    assert "__interrupt__" not in final
    assert final["run_outcome"] == "success"
    assert final["architect_decision"] == "approved"


def test_interactive_gate_revise_halts(tmp_path):
    cfg = make_config(tmp_path, headless=False)
    model = FakeToolModel(_write_decisions_response())
    ctx = RuntimeCtx(
        FakeCompiler([FakeCompileResult(0)]), FakeFixer(), _real_clusterer(), architect_model_override=model
    )
    graph = build_graph(cfg, ctx).compile(checkpointer=InMemorySaver())
    run_config = {"configurable": {"thread_id": "arch-revise"}, "recursion_limit": recursion_limit(cfg)}
    initial = {"spring_repo": str(cfg.spring_repo), "retry_count": 0, "total_llm_calls": 0}

    graph.invoke(initial, config=run_config)
    final = graph.invoke(Command(resume="revise"), config=run_config)

    assert "__interrupt__" not in final
    assert final["architect_decision"] == "revise"
    assert final["run_outcome"] == "slice_failures"


def test_interactive_gate_garbage_resume_defaults_to_revise(tmp_path):
    cfg = make_config(tmp_path, headless=False)
    model = FakeToolModel(_write_decisions_response())
    ctx = RuntimeCtx(
        FakeCompiler([FakeCompileResult(0)]), FakeFixer(), _real_clusterer(), architect_model_override=model
    )
    graph = build_graph(cfg, ctx).compile(checkpointer=InMemorySaver())
    run_config = {"configurable": {"thread_id": "arch-garbage"}, "recursion_limit": recursion_limit(cfg)}
    initial = {"spring_repo": str(cfg.spring_repo), "retry_count": 0, "total_llm_calls": 0}

    graph.invoke(initial, config=run_config)
    final = graph.invoke(Command(resume="banana"), config=run_config)

    assert "__interrupt__" not in final
    assert final["architect_decision"] == "revise"
