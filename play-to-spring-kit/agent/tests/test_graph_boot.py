"""Graph-level boot-verification phase routing (M4 Task 3): backward-compat
no-op (no boot_runner configured), first-attempt success, one failed boot
recovered by the runtime_wiring agent, attempts-exhausted (blocking, unlike
routes/config_mapping), and budget-exhausted abort mid-loop.

Follows test_graph_config_mapping.py's style: FakeCompiler/FakeFixer/
FakeToolModel + RuntimeCtx(...) + build_graph(cfg, ctx).compile() +
graph.invoke(...), plus a FakeBootRunner standing in for ctx.boot_runner.
"""

from agent.config import AgentConfig
from agent.graph import RuntimeCtx, build_graph, recursion_limit
from agent.state import EXIT_OK, EXIT_STUCK_NO_LLM
from agent.tests.test_graph_flow import (
    FakeCompileResult,
    FakeCompiler,
    FakeFixer,
    FakeToolModel,
    _real_clusterer,
)
from agent.tools.maven import BootResult
from langchain_core.messages import AIMessage


class FakeBootRunner:
    """Scripted stand-in for ctx.boot_runner: one BootResult per call."""

    def __init__(self, results: list[BootResult]):
        self.results = list(results)
        self.calls = 0

    def __call__(self, config):
        self.calls += 1
        return self.results.pop(0)


class RaisingModel:
    """Fails the test loudly if the runtime_wiring (or any) agent is ever invoked."""

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        raise AssertionError("LLM should not have been invoked in this scenario")


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


# ----------------------------------------------------------------------
# 1. Regression guard: no boot_runner configured (every pre-M4-Task-3 test's
#    RuntimeCtx shape) -> the boot phase is a silent no-op, run still
#    succeeds exactly as before this task existed.
# ----------------------------------------------------------------------


def test_no_boot_runner_configured_run_still_succeeds(tmp_path):
    cfg = make_config(tmp_path)
    ctx = RuntimeCtx(FakeCompiler([FakeCompileResult(0)]), FakeFixer(), _real_clusterer())
    final = run(cfg, ctx)
    assert final["outcome"] == "success"
    assert final["exit_code"] == EXIT_OK
    assert final["run_outcome"] == "success"
    assert final["run_exit_code"] == EXIT_OK
    assert final.get("boot_started") is True
    assert final.get("runtime_wiring_attempts", 0) == 0


# ----------------------------------------------------------------------
# 2. Boot succeeds on the first attempt: run succeeds, no agent involvement.
# ----------------------------------------------------------------------


def test_boot_succeeds_first_attempt(tmp_path):
    cfg = make_config(tmp_path)
    boot_runner = FakeBootRunner([BootResult(started=True, log_tail="Started App in 1.0 seconds")])
    ctx = RuntimeCtx(
        FakeCompiler([FakeCompileResult(0)]),
        FakeFixer(),
        _real_clusterer(),
        model_override=RaisingModel(),
        boot_runner=boot_runner,
    )
    final = run(cfg, ctx)
    assert final["run_outcome"] == "success"
    assert final["run_exit_code"] == EXIT_OK
    assert final["boot_started"] is True
    assert final.get("runtime_wiring_attempts", 0) == 0
    assert boot_runner.calls == 1


# ----------------------------------------------------------------------
# 3. Boot fails once, runtime_wiring agent invoked, second boot succeeds.
# ----------------------------------------------------------------------


def test_boot_fails_once_then_agent_fixes_it(tmp_path):
    cfg = make_config(tmp_path)
    boot_runner = FakeBootRunner(
        [
            BootResult(started=False, log_tail="NoSuchBeanDefinitionException: RestTemplate"),
            BootResult(started=True, log_tail="Started App in 1.2 seconds"),
        ]
    )
    model = FakeToolModel([AIMessage(content="added RestTemplateConfig")])
    ctx = RuntimeCtx(
        FakeCompiler([FakeCompileResult(0)]),
        FakeFixer(),
        _real_clusterer(),
        model_override=model,
        boot_runner=boot_runner,
    )
    final = run(cfg, ctx)
    assert final["run_outcome"] == "success"
    assert final["run_exit_code"] == EXIT_OK
    assert final["runtime_wiring_attempts"] == 1
    assert final["total_llm_calls"] == 1
    assert boot_runner.calls == 2


# ----------------------------------------------------------------------
# 4. Boot never succeeds within max_runtime_wiring_attempts: BLOCKING
#    (unlike routes/config_mapping) -- the run genuinely fails.
# ----------------------------------------------------------------------


def test_boot_never_succeeds_run_fails_blocking(tmp_path):
    cfg = make_config(tmp_path, max_runtime_wiring_attempts=2)
    boot_runner = FakeBootRunner(
        [
            BootResult(started=False, log_tail="fail 1"),
            BootResult(started=False, log_tail="fail 2"),
            BootResult(started=False, log_tail="fail 3"),
        ]
    )
    model = FakeToolModel([AIMessage(content="tried"), AIMessage(content="tried again")])
    ctx = RuntimeCtx(
        FakeCompiler([FakeCompileResult(0)]),
        FakeFixer(),
        _real_clusterer(),
        model_override=model,
        boot_runner=boot_runner,
    )
    final = run(cfg, ctx)
    assert final["run_outcome"] == "runtime_wiring_failed"
    assert final["run_exit_code"] == 5
    assert final["runtime_wiring_attempts"] == 2
    assert final["total_llm_calls"] == 2
    assert boot_runner.calls == 3


# ----------------------------------------------------------------------
# 4b. No API key configured: runtime_wiring_node must degrade to a terminal
#     "no_llm" outcome (mirroring guard_node's equivalent check) rather than
#     crashing make_model -- this is what keeps test_integration_mvn.py's
#     real-mvn tests green, but neither of those tests actually asserts
#     run_outcome/run_exit_code (they check the earlier, stale per-slice
#     outcome/exit_code instead), so assert the exact shape here directly.
# ----------------------------------------------------------------------


def test_boot_fails_with_no_api_key_degrades_to_no_llm(tmp_path):
    cfg = make_config(tmp_path)
    cfg.api_key = None
    boot_runner = FakeBootRunner([BootResult(started=False, log_tail="NoSuchBeanDefinitionException")])
    ctx = RuntimeCtx(
        FakeCompiler([FakeCompileResult(0)]),
        FakeFixer(),
        _real_clusterer(),
        model_override=RaisingModel(),
        boot_runner=boot_runner,
    )
    final = run(cfg, ctx)
    assert final["run_outcome"] == "no_llm"
    assert final["run_exit_code"] == EXIT_STUCK_NO_LLM
    assert final.get("runtime_wiring_attempts", 0) == 0
    assert final["total_llm_calls"] == 0
    assert boot_runner.calls == 1


# ----------------------------------------------------------------------
# 5. Global budget exhausted during the runtime-wiring loop aborts the run
#    (checked before the per-phase attempts cap, per _phase_budget_decision).
# ----------------------------------------------------------------------


def test_runtime_wiring_respects_global_budget_zero_never_invokes_agent(tmp_path):
    cfg = make_config(tmp_path, max_total_llm_calls=0)
    boot_runner = FakeBootRunner([BootResult(started=False, log_tail="fail")])
    ctx = RuntimeCtx(
        FakeCompiler([FakeCompileResult(0)]),
        FakeFixer(),
        _real_clusterer(),
        model_override=RaisingModel(),
        boot_runner=boot_runner,
    )
    final = run(cfg, ctx)
    assert final["run_outcome"] == "budget_exhausted"
    assert final["run_exit_code"] == 4
    assert final["total_llm_calls"] == 0
    assert final.get("runtime_wiring_attempts", 0) == 0


def test_runtime_wiring_stops_after_budget_exhausted_mid_loop(tmp_path):
    """Regression (mirrors routes/config_mapping's analogous test): with
    max_total_llm_calls=1, exactly 1 LLM call is made, then the run halts
    with budget_exhausted rather than looping past the global budget on the
    strength of max_runtime_wiring_attempts alone."""
    cfg = make_config(tmp_path, max_total_llm_calls=1, max_runtime_wiring_attempts=5)
    boot_runner = FakeBootRunner(
        [
            BootResult(started=False, log_tail="fail 1"),
            BootResult(started=False, log_tail="fail 2"),
        ]
    )
    # Only one response queued: a second model.invoke() would raise IndexError.
    model = FakeToolModel([AIMessage(content="tried")])
    ctx = RuntimeCtx(
        FakeCompiler([FakeCompileResult(0)]),
        FakeFixer(),
        _real_clusterer(),
        model_override=model,
        boot_runner=boot_runner,
    )
    final = run(cfg, ctx)
    assert final["run_outcome"] == "budget_exhausted"
    assert final["run_exit_code"] == 4
    assert final["total_llm_calls"] == 1
    assert final["runtime_wiring_attempts"] == 1
    assert boot_runner.calls == 2
