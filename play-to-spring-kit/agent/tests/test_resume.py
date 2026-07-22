"""Resumability: checkpointed run survives a process 'restart' (new graph.invoke,
same thread_id, same sqlite file) without repeating already-done work."""

from pathlib import Path

from agent.checkpoint import make_checkpointer, thread_id_for
from agent.config import AgentConfig
from agent.graph import RuntimeCtx, build_graph, recursion_limit
from agent.tests.test_graph_flow import FakeCompileResult, FakeCompiler, FakeFixer, _real_clusterer


def test_resume_after_success_is_idempotent(tmp_path):
    cfg = AgentConfig(spring_repo=tmp_path)
    cfg.api_key = "test-key"
    checkpointer = make_checkpointer(cfg)
    thread_id = thread_id_for(cfg)
    run_config = {"configurable": {"thread_id": thread_id}, "recursion_limit": recursion_limit(cfg)}

    ctx1 = RuntimeCtx(FakeCompiler([FakeCompileResult(0)]), FakeFixer(), _real_clusterer())
    graph1 = build_graph(cfg, ctx1).compile(checkpointer=checkpointer)
    initial = {"spring_repo": str(tmp_path), "slice_id": "r", "retry_count": 0, "total_llm_calls": 0}
    final1 = graph1.invoke(initial, config=run_config)
    assert final1["outcome"] == "success"

    # "Restart": new checkpointer/graph pointed at the same sqlite file + thread_id.
    checkpointer2 = make_checkpointer(cfg)
    ctx2 = RuntimeCtx(FakeCompiler([]), FakeFixer(), _real_clusterer())  # would error if re-invoked
    graph2 = build_graph(cfg, ctx2).compile(checkpointer=checkpointer2)
    state = graph2.get_state(run_config)
    assert state.values["outcome"] == "success"
    assert ctx2.compiler.calls == 0  # nothing re-ran
