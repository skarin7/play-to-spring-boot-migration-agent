"""Migration engine as a LangGraph state graph.

Topology (M4 — routes phase wraps the M1 compile-fix loop a second time,
after the M2 slice pipeline finishes, via a generic "fix cycle" re-entry
mechanism any future phase can reuse — see the note below the diagram):

    START -> inventory -+-> slice_router -+-> transform -> compile -+-> slice_finalize
                        |  (no slices)     |  (all done)             |
                        +-> run_halt(6)    +-> routes_node            |
                                                                      |
                        slice_router <-- signature_check <-- slice_finalize (continue)
                                          run_halt <-------- slice_finalize (abort: budget/infra/no_llm)

    signature_check (M6): deterministic T2 tier, no LLM -- see nodes/signature_check.py.
    Slice-scoped after slice_finalize; unscoped again as signature_check_final
    between verify and boot_run. Findings accumulate in state.signature_findings
    and never gate the run by themselves (soft, like routes/config_mapping).

    routes_node -+-> routes_node        (self-loop: more unmapped routes, budget left)
                 +-> routes_fix_prep -> compile   (re-enter compile-fix subgraph, phase=fix_cycle)
                 +-> config_mapping                (no play_repo / no conf/routes: no-op)
                 +-> run_halt                      (routes_node itself hit the global LLM budget)

    after_fix_cycle -+-> <fix_cycle_return_to>  (phase reset to "slice"; non-blocking unless budget_exhausted)
                      +-> run_halt               (budget_exhausted only)

    config_mapping -+-> config_mapping  (self-loop: leftover config keys, budget left)
                     +-> verify          (no leftover / attempts exhausted / no conf-export to map)
                     +-> run_halt        (config_mapping itself hit the global LLM budget)

    verify -+-> boot_run
            +-> run_halt

    boot_run -+-> run_done                       (app started: "final_verification" is a routing
                                                    label, not a node -- boot_started IS the check)
              +-> runtime_wiring                  (app never printed the Spring Boot startup line)

    runtime_wiring -+-> boot_run   (agent round done, re-check boot; always loops back)
                     +-> run_halt  (own budget (6) or the global LLM budget exhausted: THIS phase's
                                     failure genuinely fails the whole run -- unlike routes/config_mapping,
                                     "the app never starts" is not a non-blocking outcome)

    compile -+-> done ----------------------> slice_finalize | after_fix_cycle (route_by_phase)
             +-> human_gate -+-> infra -----> slice_finalize | after_fix_cycle (route_by_phase)
                             +-> compile       (retry, interactive mode only)
             +-> det_fix -+-> compile          (deterministic re-loop)
                          +-> cluster -> guard -+-> agent -> compile
                                                +-> halt --> slice_finalize | after_fix_cycle (route_by_phase)

Determinism-first: the LLM agent is reached only after the deterministic
fixers made no progress, and only if the guard (budget / retries / timeout /
fingerprint loop detection) allows it. The compile-fix subgraph itself is
phase-agnostic: `state["phase"]` ("slice" default | "fix_cycle") only changes
where done/infra/halt exit to (route_by_phase), never their own logic.

human_gate (M5) sits between compile's infra branch and the infra node
itself: in headless mode (config.headless=True, the default, used by every
automated/CI run) it is a no-op pass-through straight to "infra", identical
to pre-M5 behavior; in interactive mode (--interactive) it pauses the graph
via interrupt() so a human operator can inspect the failure and either
retry the compile (in case it was a transient toolchain crash) or accept it
as a hard infrastructure failure via the same "infra" node either way.

Generic fix-cycle re-entry (any phase, not just routes, that needs to make
edits and then re-verify via the shared compile-fix subgraph): a phase's own
"<phase>_fix_prep" node (e.g. routes_fix_prep) sets phase="fix_cycle" and
fix_cycle_return_to="<node to resume at>", then edges to "compile". When the
subgraph terminates (done/infra/halt), route_by_phase sends it to the single
generic after_fix_cycle node, which aborts the whole run on
outcome=="budget_exhausted" (legacy RUN_ABORTING_OUTCOMES parity) or
otherwise resets phase back to "slice" and hands control to whatever
fix_cycle_return_to says (route_after_fix_cycle) — every possible
fix_cycle_return_to target must be enumerated once in that edge's mapping
dict, but no new node or router is needed per phase.

Per-slice vs run-level state (legacy parity: migration_orchestrator.py's
`for label, le, ... in entity_iterable` loop at :2500 vs. the `while True`
compile-fix loop nested inside it at :2530):
  - migration_units, source_inventory, total_llm_calls,
    excluded_error_signatures persist across the whole run.
  - retry_count, error_fingerprints, det_fix_log, last_compile, last_clusters,
    last_edited_files, slice_started_at, outcome, exit_code are reset by
    slice_router each time it advances to a new unit, and again by any
    "<phase>_fix_prep" node before its fix-cycle re-entry.
  - budget_exhausted / infrastructure_error / no_llm abort the whole run
    immediately; every other per-slice outcome just ends that slice and lets
    slice_router continue to the next unit (legacy: `return N` vs `break`).
    Inside a fix cycle only budget_exhausted aborts the run (after_fix_cycle);
    infra/no_llm there are non-blocking (logged; e.g. routes mapping is
    best-effort and never gates the run — routes were never part of the
    legacy tool's automated scope to begin with). config_mapping is
    similarly best-effort and non-blocking (it only edits .properties
    values, so it never needs a fix-cycle re-entry of its own — it just
    self-loops or gives up and proceeds straight to verify). runtime_wiring
    (M4) is the one exception to "non-blocking": verify only proves the code
    compiles, so boot_run actually starts the Spring app (agent/tools/maven.py)
    and, if it never prints the startup line within its own budget of
    attempts, run_halt with outcome="runtime_wiring_failed" — the plan
    requires overall run success to gate on the app actually starting.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph

from .config import AgentConfig
from .nodes import architect as architect_nodes
from .nodes import bootstrap as bootstrap_nodes
from .nodes import boot as boot_nodes
from .nodes import endpoint_parity as endpoint_parity_nodes
from .nodes import config_mapping as config_mapping_nodes
from .nodes import fix_loop
from .nodes import human_gate as human_gate_nodes
from .nodes import routes as routes_nodes
from .nodes import slice_pipeline as slice_pipeline_nodes
from .nodes.architect import (
    route_after_architect_check,
    route_after_architect_gate,
    route_after_architect_verify,
)
from .nodes.boot import route_after_boot_run, route_after_runtime_wiring_node
from .nodes.bootstrap import route_after_bootstrap_check, route_after_bootstrap_verify, route_after_setup
from .nodes.common import after_fix_cycle_node, route_after_fix_cycle, route_by_phase
from .nodes.config_mapping import route_after_config_mapping_node
from .nodes.fix_loop import (
    done_node,
    halt_node,
    infra_node,
    play_repo_tampered_node,
    route_after_compile,
    route_after_det_fix,
    route_after_guard,
)
from .nodes.human_gate import route_after_human_gate
from .nodes.routes import route_after_routes_node, routes_fix_prep_node
from .nodes.signature_check import build as build_signature_check_nodes
from .nodes.slice_pipeline import (
    route_after_inventory,
    route_after_slice_finalize,
    route_after_slice_router,
    route_after_verify,
    run_done_node,
    run_halt_node,
    slice_router_node,
)
from .state import MigrationState
from .tools.maven import BootResult, TestResult

JarRunner = Callable[[AgentConfig, str], "tuple[int, int]"]
BootRunner = Callable[[AgentConfig], BootResult]
InventoryRunner = Callable[[AgentConfig], "dict[str, Any] | None"]
SignatureRunner = Callable[
    [AgentConfig, "Any", "Any"], "tuple[dict[str, Any] | None, dict[str, Any] | None]"
]
TestRunner = Callable[[AgentConfig], TestResult]
# Returns None if the runner couldn't boot both apps (logged, not an error).
# Otherwise (diff_entries, unproved, probes_compared) -- see
# nodes/endpoint_parity.py and tools/endpoint_diff.py.
EndpointParityRunner = Callable[
    [AgentConfig, "list[dict[str, Any]]"], "tuple[list[dict[str, Any]], list[dict[str, Any]], int] | None"
]


@dataclass
class RuntimeCtx:
    """Process-local components (not checkpointed). Injectable for tests."""

    compiler: Any
    fixer: Any
    clusterer: Any
    model_override: Any = None
    jar_runner: JarRunner | None = None
    setup_ops: Any = None
    bootstrap_model_override: Any = None
    architect_model_override: Any = None
    boot_runner: BootRunner | None = None
    inventory_runner: InventoryRunner | None = None
    signature_runner: SignatureRunner | None = None
    test_runner: TestRunner | None = None
    # No default implementation wired in default_ctx() (unlike every other
    # runner above) -- dual-boot Play+Spring orchestration is repo-dependent
    # in a way this codebase has no existing wrapper for. None here means T5
    # degrades to "not_attempted", never a run-blocking condition. See
    # nodes/endpoint_parity.py's module docstring.
    endpoint_parity_runner: EndpointParityRunner | None = None


def _default_jar_runner(config: AgentConfig, path_prefix: str) -> tuple[int, int]:
    from .tools.journal import write_entry
    from .tools.toolkit_jar import migrate_until_done

    if config.play_repo is None or config.jar_path is None or not config.jar_path.is_file():
        return 0, 0

    # M6 Task 7: journal key derived from path_prefix, not the JarRunner
    # call's caller-side unit id -- the public JarRunner signature
    # (Callable[[AgentConfig, str], tuple[int, int]]) is exercised directly
    # by several existing test fixtures with 2-arg lambdas, so it stays
    # unchanged; this keeps the per-batch write entirely inside the default
    # implementation. path_prefix is already the per-unit distinguishing
    # value transform_node passes in, so it's a stable-enough journal key.
    unit_key = path_prefix or "app_root"

    def on_batch(n: int, m: int, remaining: int) -> None:
        write_entry(
            config.spring_repo,
            unit_key,
            {"unit": unit_key, "action": "migrated", "count": n, "remaining": remaining},
        )
        if m:
            write_entry(config.spring_repo, unit_key, {"unit": unit_key, "action": "compiled", "error_count": m})

    return migrate_until_done(
        config.play_repo,
        config.jar_path,
        config.spring_repo,
        config.migrate_batch_size,
        config.dry_run,
        path_prefix=path_prefix,
        on_batch=on_batch,
    )


def _default_inventory_runner(config: AgentConfig) -> "dict[str, Any] | None":
    """Pre-flight Play-surface scan, run once before the first slice transform.

    Returns None (no signal, not a failure) if there's no play_repo/jar to scan with --
    callers must treat a missing report the same as an empty one, never abort on it.
    """
    from .tools.toolkit_inventory import run_inventory_scan

    if config.play_repo is None or config.jar_path is None or not config.jar_path.is_file():
        return None
    report_path = config.migration_dir / "play-surface-inventory.json"
    return run_inventory_scan(config.play_repo, config.jar_path, report_path, config.dry_run)


def _default_signature_runner(
    config: AgentConfig, play_root: Any, spring_root: Any
) -> "tuple[dict[str, Any] | None, dict[str, Any] | None]":
    """Runs `signature` twice (Play root, Spring root) via two temp report
    files under .migration/ -- separate files, not one shared path, so a
    slice-scoped call racing a later final call never clobbers the other's
    still-being-read report."""
    from .tools.signature_diff import run_signature_scan

    if config.jar_path is None:
        return None, None
    play_report_path = config.migration_dir / "signature-play.json"
    spring_report_path = config.migration_dir / "signature-spring.json"
    play_report = run_signature_scan(play_root, config.jar_path, play_report_path, config.dry_run)
    spring_report = run_signature_scan(spring_root, config.jar_path, spring_report_path, config.dry_run)
    return play_report, spring_report


def default_ctx(config: AgentConfig) -> RuntimeCtx:
    from compile_error_fixer import CompileErrorFixer
    from error_clusterer import ErrorClusterer
    from incremental_compiler import IncrementalCompiler

    from .tools.maven import run_mvn_test, run_spring_boot
    from .tools.setup_ops import SetupOps

    return RuntimeCtx(
        compiler=IncrementalCompiler(config.spring_repo, dry_run=config.dry_run),
        fixer=CompileErrorFixer(config.spring_repo, dry_run=config.dry_run),
        clusterer=ErrorClusterer(),
        jar_runner=_default_jar_runner,
        setup_ops=SetupOps(),
        boot_runner=lambda cfg: run_spring_boot(cfg.spring_repo, cfg.boot_timeout_sec, cfg.dry_run),
        inventory_runner=_default_inventory_runner,
        signature_runner=_default_signature_runner,
        test_runner=lambda cfg: run_mvn_test(cfg.spring_repo, cfg.test_timeout_sec, cfg.dry_run),
    )


def build_graph(config: AgentConfig, ctx: RuntimeCtx | None = None):
    ctx = ctx or default_ctx(config)

    b = bootstrap_nodes.build(config, ctx)
    arch = architect_nodes.build(config, ctx)
    sp = slice_pipeline_nodes.build(config, ctx)
    fl = fix_loop.build(config, ctx)
    rt = routes_nodes.build(config, ctx)
    cm = config_mapping_nodes.build(config, ctx)
    bt = boot_nodes.build(config, ctx)
    hg = human_gate_nodes.build(config, ctx)
    sc = build_signature_check_nodes(config, ctx)
    ep = endpoint_parity_nodes.build(config, ctx)

    # ------------------------------------------------------------------
    # Wiring
    # ------------------------------------------------------------------

    g = StateGraph(MigrationState)
    g.add_node("setup", b["setup_node"])
    g.add_node("bootstrap_check", b["bootstrap_check_node"])
    g.add_node("bootstrap_agent", b["bootstrap_agent_node"])
    g.add_node("bootstrap_verify", b["bootstrap_verify_node"])

    g.add_node("inventory", sp["inventory_node"])
    g.add_node("architect_check", arch["architect_check_node"])
    g.add_node("architect_agent", arch["architect_agent_node"])
    g.add_node("architect_verify", arch["architect_verify_node"])
    g.add_node("architect_gate", arch["architect_gate_node"])
    g.add_node("slice_router", slice_router_node)
    g.add_node("transform", sp["transform_node"])
    g.add_node("slice_finalize", sp["slice_finalize_node"])
    g.add_node("signature_check", sc["signature_check_node"])
    g.add_node("signature_check_final", sc["signature_check_final_node"])
    g.add_node("routes", rt["routes_node"])
    g.add_node("routes_fix_prep", routes_fix_prep_node)
    g.add_node("after_fix_cycle", after_fix_cycle_node)
    g.add_node("config_mapping", cm["config_mapping_node"])
    g.add_node("verify", sp["verify_node"])
    g.add_node("boot_run", bt["boot_run_node"])
    g.add_node("runtime_wiring", bt["runtime_wiring_node"])
    g.add_node("endpoint_parity", ep["endpoint_parity_node"])
    g.add_node("play_repo_tampered", play_repo_tampered_node)
    g.add_node("run_done", run_done_node)
    g.add_node("run_halt", run_halt_node)

    g.add_node("compile", fl["compile_node"])
    g.add_node("det_fix", fl["det_fix_node"])
    g.add_node("cluster", fl["cluster_node"])
    g.add_node("guard", fl["guard_node"])
    g.add_node("agent", fl["agent_node"])
    g.add_node("done", done_node)
    g.add_node("human_gate", hg["human_gate_node"])
    g.add_node("infra", infra_node)
    g.add_node("halt", halt_node)

    g.add_edge(START, "setup")
    g.add_conditional_edges("setup", route_after_setup, {"bootstrap_check": "bootstrap_check", "halt": "run_halt"})
    g.add_conditional_edges(
        "bootstrap_check", route_after_bootstrap_check, {"inventory": "inventory", "bootstrap_agent": "bootstrap_agent"}
    )
    g.add_edge("bootstrap_agent", "bootstrap_verify")
    g.add_conditional_edges(
        "bootstrap_verify",
        route_after_bootstrap_verify,
        {"inventory": "inventory", "bootstrap_agent": "bootstrap_agent", "run_halt": "run_halt"},
    )

    g.add_conditional_edges(
        "inventory", route_after_inventory, {"router": "architect_check", "no_slices": "run_halt"}
    )
    g.add_conditional_edges(
        "architect_check",
        route_after_architect_check,
        {"skip": "slice_router", "architect_agent": "architect_agent"},
    )
    g.add_edge("architect_agent", "architect_verify")
    g.add_conditional_edges(
        "architect_verify",
        route_after_architect_verify,
        {"gate": "architect_gate", "architect_agent": "architect_agent", "run_halt": "run_halt"},
    )
    g.add_conditional_edges(
        "architect_gate",
        route_after_architect_gate,
        {"inventory_done": "slice_router", "run_halt": "run_halt"},
    )
    g.add_conditional_edges("slice_router", route_after_slice_router, {"transform": "transform", "routes": "routes"})
    g.add_edge("transform", "compile")

    g.add_conditional_edges(
        "compile",
        route_after_compile,
        {
            "done": "done",
            "infra": "human_gate",
            "det_fix": "det_fix",
            "halt": "halt",
            "play_repo_tampered": "play_repo_tampered",
        },
    )
    g.add_conditional_edges(
        "human_gate", route_after_human_gate, {"compile": "compile", "infra": "infra"}
    )
    g.add_conditional_edges(
        "det_fix", route_after_det_fix, {"compile": "compile", "cluster": "cluster"}
    )
    g.add_edge("cluster", "guard")
    g.add_conditional_edges("guard", route_after_guard, {"agent": "agent", "halt": "halt"})
    g.add_edge("agent", "compile")
    g.add_conditional_edges(
        "done", route_by_phase, {"slice_finalize": "slice_finalize", "after_fix_cycle": "after_fix_cycle"}
    )
    g.add_conditional_edges(
        "infra", route_by_phase, {"slice_finalize": "slice_finalize", "after_fix_cycle": "after_fix_cycle"}
    )
    g.add_conditional_edges(
        "halt", route_by_phase, {"slice_finalize": "slice_finalize", "after_fix_cycle": "after_fix_cycle"}
    )
    g.add_conditional_edges(
        "slice_finalize", route_after_slice_finalize, {"router": "signature_check", "halt": "run_halt"}
    )
    g.add_edge("signature_check", "slice_router")

    g.add_conditional_edges(
        "routes",
        route_after_routes_node,
        {
            "routes": "routes",
            "routes_fix_prep": "routes_fix_prep",
            "config_mapping": "config_mapping",
            "halt": "run_halt",
        },
    )
    g.add_edge("routes_fix_prep", "compile")
    # Every phase's fix_cycle_return_to value must be enumerated here (one
    # line per future phase, in this one place) — LangGraph needs the full
    # destination set upfront.
    g.add_conditional_edges(
        "after_fix_cycle", route_after_fix_cycle, {"halt": "run_halt", "config_mapping": "config_mapping"}
    )
    g.add_conditional_edges(
        "config_mapping",
        route_after_config_mapping_node,
        {"config_mapping": "config_mapping", "verify": "verify", "halt": "run_halt"},
    )

    g.add_conditional_edges("verify", route_after_verify, {"halt": "run_halt", "boot_run": "signature_check_final"})
    g.add_edge("signature_check_final", "boot_run")
    g.add_conditional_edges(
        "boot_run",
        route_after_boot_run,
        {"final_verification": "endpoint_parity", "runtime_wiring": "runtime_wiring"},
    )
    g.add_edge("endpoint_parity", "run_done")
    g.add_conditional_edges(
        "runtime_wiring", route_after_runtime_wiring_node, {"halt": "run_halt", "boot_run": "boot_run"}
    )
    g.add_edge("run_done", END)
    g.add_edge("run_halt", END)
    g.add_edge("play_repo_tampered", END)
    return g


def recursion_limit(config: AgentConfig) -> int:
    # Worst case per LLM round: compile + det_fix + cluster + guard + agent = 5 nodes,
    # plus deterministic re-loops, plus a flat overhead for slice-pipeline
    # bookkeeping nodes (inventory/router/transform/finalize/verify — a handful
    # per slice, negligible next to the LLM-round budget). Generous multiple of
    # the LLM budget, capped.
    return min(150 + config.max_total_llm_calls * 8, 1000)
