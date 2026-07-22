"""Human-in-the-loop gate tests (M5): headless pass-through vs. interactive
interrupt()/resume for infrastructure compile errors.

Test 1 is the critical backward-compat anchor: headless mode (the default)
must behave exactly as it did before human_gate existed, and must work with
a graph compiled WITHOUT a checkpointer -- proving interrupt() is never
reached on that path (all 113 pre-existing tests rely on this).
"""

from dataclasses import dataclass, field
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agent.config import AgentConfig
from agent.graph import RuntimeCtx, build_graph, recursion_limit
from agent.state import EXIT_INFRASTRUCTURE


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

    def compile(self, changed_files=None):
        self.calls += 1
        return self.results.pop(0) if self.results else FakeCompileResult(0)


class FakeFixer:
    def run(self, errors):
        raise AssertionError("det_fix should not run for an infra-error compile result")

    def revert_bad_fixes(self, errors):
        return []

    def delete_bak_files(self):
        pass


def _real_clusterer():
    from error_clusterer import ErrorClusterer

    return ErrorClusterer()


def make_config(tmp_path, **overrides) -> AgentConfig:
    cfg = AgentConfig(spring_repo=tmp_path)
    cfg.api_key = "test-key"
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def _initial_state(cfg: AgentConfig) -> dict:
    return {
        "spring_repo": str(cfg.spring_repo),
        "slice_id": "t",
        "retry_count": 0,
        "total_llm_calls": 0,
    }


INFRA_RESULT = FakeCompileResult(1, [], log="fatal error compiling", infra=True)


def test_infra_error_headless_still_halts_without_checkpointer(tmp_path):
    """Backward-compat regression: headless (default) never calls interrupt(),
    so the graph works even when compiled without a checkpointer -- exactly
    like test_graph_flow.py's test_infrastructure_error_halts."""
    cfg = make_config(tmp_path)
    assert cfg.headless is True
    ctx = RuntimeCtx(FakeCompiler([INFRA_RESULT]), FakeFixer(), _real_clusterer())

    graph = build_graph(cfg, ctx).compile()  # no checkpointer
    final = graph.invoke(
        _initial_state(cfg), config={"recursion_limit": recursion_limit(cfg)}
    )

    assert "__interrupt__" not in final
    assert final["outcome"] == "infrastructure_error"
    assert final["exit_code"] == EXIT_INFRASTRUCTURE


def test_interactive_infra_error_pauses_then_aborts(tmp_path):
    cfg = make_config(tmp_path, headless=False)
    ctx = RuntimeCtx(FakeCompiler([INFRA_RESULT]), FakeFixer(), _real_clusterer())

    graph = build_graph(cfg, ctx).compile(checkpointer=InMemorySaver())
    run_config = {"configurable": {"thread_id": "abort-thread"}, "recursion_limit": recursion_limit(cfg)}

    final = graph.invoke(_initial_state(cfg), config=run_config)
    assert "__interrupt__" in final
    itr = final["__interrupt__"][0]
    assert itr.value["reason"] == "infrastructure_error"

    final = graph.invoke(Command(resume="abort"), config=run_config)
    assert "__interrupt__" not in final
    assert final["outcome"] == "infrastructure_error"
    assert final["exit_code"] == EXIT_INFRASTRUCTURE


def test_interactive_infra_error_retry_reaches_success(tmp_path):
    cfg = make_config(tmp_path, headless=False)
    compiler = FakeCompiler([INFRA_RESULT, FakeCompileResult(0)])
    ctx = RuntimeCtx(compiler, FakeFixer(), _real_clusterer())

    graph = build_graph(cfg, ctx).compile(checkpointer=InMemorySaver())
    run_config = {"configurable": {"thread_id": "retry-thread"}, "recursion_limit": recursion_limit(cfg)}

    final = graph.invoke(_initial_state(cfg), config=run_config)
    assert "__interrupt__" in final
    calls_before_resume = compiler.calls

    final = graph.invoke(Command(resume="retry"), config=run_config)
    assert "__interrupt__" not in final
    assert final["outcome"] == "success"
    assert compiler.calls > calls_before_resume


def test_interactive_infra_error_garbage_resume_defaults_to_abort(tmp_path):
    cfg = make_config(tmp_path, headless=False)
    ctx = RuntimeCtx(FakeCompiler([INFRA_RESULT]), FakeFixer(), _real_clusterer())

    graph = build_graph(cfg, ctx).compile(checkpointer=InMemorySaver())
    run_config = {"configurable": {"thread_id": "garbage-thread"}, "recursion_limit": recursion_limit(cfg)}

    final = graph.invoke(_initial_state(cfg), config=run_config)
    assert "__interrupt__" in final

    final = graph.invoke(Command(resume="banana"), config=run_config)
    assert "__interrupt__" not in final
    assert final["outcome"] == "infrastructure_error"
    assert final["exit_code"] == EXIT_INFRASTRUCTURE
