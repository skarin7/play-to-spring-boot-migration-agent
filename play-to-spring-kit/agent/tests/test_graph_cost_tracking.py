"""Graph-level total_cost_usd accumulation (M6 Task 5): an LLM round with
usage_metadata produces a priced total_cost_usd in final state, and status_v2
surfaces it next to total_llm_calls."""

from langchain_core.messages import AIMessage

from agent.config import AgentConfig
from agent.graph import RuntimeCtx, build_graph, recursion_limit
from agent.state import EXIT_BUDGET_EXHAUSTED
from agent.status_v2 import state_to_status_v2
from agent.tests.test_graph_flow import ERR, FakeCompileResult, FakeCompiler, FakeFixer, FakeToolModel, _real_clusterer


def make_config(tmp_path, **overrides) -> AgentConfig:
    cfg = AgentConfig(spring_repo=tmp_path)
    cfg.api_key = "test-key"
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def run(cfg, ctx):
    graph = build_graph(cfg, ctx).compile()
    initial = {"spring_repo": str(cfg.spring_repo), "slice_id": "t", "retry_count": 0, "total_llm_calls": 0}
    return graph.invoke(initial, config={"recursion_limit": recursion_limit(cfg)})


def test_llm_round_accumulates_cost(tmp_path):
    # Explicit model_cheap so this test's outcome doesn't depend on
    # MIGRATION_MODEL_CHEAP in the environment (this repo's own .env pins it
    # to a model with no pricing.py rate-table entry).
    cfg = make_config(tmp_path, model_cheap="anthropic/claude-haiku-4.5")
    compiler = FakeCompiler([FakeCompileResult(1, [ERR]), FakeCompileResult(0)])
    ctx = RuntimeCtx(
        compiler,
        FakeFixer([0]),
        _real_clusterer(),
        model_override=FakeToolModel(
            [AIMessage(content="fixed it", usage_metadata={"input_tokens": 1000, "output_tokens": 100, "total_tokens": 1100})]
        ),
    )
    final = run(cfg, ctx)

    assert final["outcome"] == "success"
    assert final["total_llm_calls"] == 1
    assert final["total_cost_usd"] > 0


def test_no_llm_round_leaves_cost_at_zero(tmp_path):
    cfg = make_config(tmp_path)
    ctx = RuntimeCtx(FakeCompiler([FakeCompileResult(0)]), FakeFixer(), _real_clusterer())
    final = run(cfg, ctx)
    assert final.get("total_cost_usd", 0.0) == 0.0


def test_cost_budget_halts_run_before_llm_round(tmp_path):
    cfg = make_config(tmp_path, max_total_cost_usd=0.000001)
    compiler = FakeCompiler([FakeCompileResult(1, [ERR])])
    ctx = RuntimeCtx(compiler, FakeFixer([0]), _real_clusterer())

    # First round hasn't run yet, so total_cost_usd starts at 0 -- this only
    # trips once state accrues cost from a prior round. Seed it directly to
    # exercise the halt path without depending on a second LLM round's exact
    # price crossing an arbitrarily tiny threshold.
    graph = build_graph(cfg, ctx).compile()
    initial = {
        "spring_repo": str(cfg.spring_repo),
        "slice_id": "t",
        "retry_count": 0,
        "total_llm_calls": 0,
        "total_cost_usd": 1.0,
    }
    final = graph.invoke(initial, config={"recursion_limit": recursion_limit(cfg)})

    assert final["outcome"] == "budget_exhausted"
    assert final["exit_code"] == EXIT_BUDGET_EXHAUSTED


def test_status_v2_surfaces_cost_next_to_call_count(tmp_path):
    cfg = make_config(tmp_path, max_total_cost_usd=10.0)
    state = {"total_llm_calls": 3, "total_cost_usd": 1.23456789, "migration_units": []}

    status = state_to_status_v2(state, cfg)

    assert status["autonomous"]["total_llm_calls"] == 3
    assert status["autonomous"]["total_cost_usd"] == 1.234568  # rounded to 6dp
    assert status["autonomous"]["max_total_cost_usd"] == 10.0
