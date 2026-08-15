"""LangGraph state schema for the migration engine.

M1 added the single-slice compile-fix loop (per-slice transient fields below).
M2 wraps it in a slice pipeline: ``migration_units`` persists per-unit progress
across the whole run (legacy parity: ``status["migration_units"]``), while the
per-slice transient fields are reset each time slice_router advances to a new
unit (legacy parity: a fresh ``le`` dict / local loop variables per iteration
of the ``for label, le, ... in entity_iterable`` loop, migration_orchestrator.py:2500).
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

GuardDecision = Literal[
    "agent", "budget_exhausted", "retries_exhausted", "timeout", "looping", "no_llm"
]

# Per-slice compile-fix outcome (M1). "budget_exhausted", "infrastructure_error"
# and "no_llm" abort the whole run immediately (legacy: unconditional `return`
# inside the slice loop); the rest just end that slice and let slice_router
# continue to the next unit (legacy: `break`, no `return`).
Outcome = Literal[
    "success",
    "failed",
    "budget_exhausted",
    "timeout",
    "looping",
    "infrastructure_error",
    "no_llm",
]

# Terminal unit statuses that must not be treated as a successful migration
# (legacy parity: migration_orchestrator.py's _SLICE_TERMINAL_FAILURE_STATUSES).
UNIT_TERMINAL_FAILURE_STATUSES = frozenset(
    ("loop_detected", "failed", "timeout", "budget_exhausted", "needs_manual_fix", "no_migrated_output")
)

# Outcomes that abort the entire run immediately rather than continuing to the
# next slice (legacy parity: the three `return N` statements inside the while
# loop, vs. every other terminal state which only `break`s that slice).
RUN_ABORTING_OUTCOMES = frozenset(("budget_exhausted", "infrastructure_error", "no_llm"))


class CompileSummary(TypedDict, total=False):
    returncode: int
    error_count: int
    infra: bool
    log_tail: str
    errors: list[dict[str, Any]]


class UnitState(TypedDict, total=False):
    """One discovered migration unit (legacy parity: an entry in ``migration_units``)."""

    id: str
    path_prefix: str
    java_file_count: int
    status: str  # pending|in_progress|compiling|done|<UNIT_TERMINAL_FAILURE_STATUSES>
    retry_count: int
    llm_calls: int
    files_migrated: int
    validate_iteration: int
    last_error_count: int | None
    failure_reason: str | None
    error_fingerprints: list[list[str]]
    det_fix_log: list[str]


class MigrationState(TypedDict, total=False):
    # identity / paths (set at START, stable)
    spring_repo: str
    play_repo: str
    slice_id: str
    slice_started_at: float  # wall-clock epoch; survives checkpoint resume

    # counters
    retry_count: int  # LLM fix rounds for the current slice
    total_llm_calls: int  # global run budget (legacy: status["autonomous"]["total_llm_calls"])
    # M6 Task 5: running USD total across the whole run, summed from each
    # agent call's pricing.cost_usd() result (0.0 for a call whose model has
    # no rate-table entry -- see pricing.py). Alongside total_llm_calls, not
    # instead of it: call count is still the pre-existing default guard.
    total_cost_usd: float

    # loop memory (legacy parity: last 5 rounds of sorted file:line:msg)
    error_fingerprints: list[list[str]]
    excluded_error_signatures: list[str]  # accumulates across the whole run, in-process only
    det_fix_log: list[str]

    # per-round transient data (current slice)
    last_compile: CompileSummary
    last_clusters: list[dict[str, Any]]
    last_edited_files: list[str]
    det_fixed_last_round: int
    # Set by the compile-fix agent itself via the flag_for_manual_review tool
    # (tools/fs.py) when it determines the failure needs a redesign, not an
    # incremental fix -- e.g. a library with no Spring/Jakarta equivalent.
    # Preferred over the deterministic unmappable_framework_packages() guess
    # in slice_finalize_node's _manual_intervention_note.
    agent_manual_review_reason: str | None

    # routing / terminal (current slice)
    guard_decision: GuardDecision
    outcome: Outcome
    exit_code: int

    # slice pipeline (M2)
    source_inventory: dict[str, Any] | None
    # Pre-flight Play-surface scan (java-dev-toolkit `inventory` subcommand): per-touchpoint
    # KNOWN/UNKNOWN/PARADIGM classification + coveragePercent, run once before the first
    # slice transform. None when no play_repo/jar (no signal, not a failure).
    play_surface_inventory: dict[str, Any] | None
    migration_units: list[UnitState]
    current_unit_idx: int
    migration_verification: dict[str, Any] | None
    run_outcome: str
    run_exit_code: int

    # Per-file/per-batch crash recovery journal (M6 Task 7): lines already
    # folded per journal file name, so re-folding on a re-dispatched unit
    # doesn't recount lines already accounted for. See tools/journal.py.
    journal_offsets: dict[str, int]

    # bootstrap (M3)
    bootstrap_attempts: int
    bootstrap_decision: str  # "skip" | "agent" | "retry" | "exhausted"

    # architect phase (M6 Task 6): runs once after inventory, before the
    # first transform. Writes .migration/decisions.md -- every later agent
    # prompt is told the path, never the contents (push paths, not bytes).
    # Interactive mode gates approval via interrupt(); headless auto-approves
    # and logs that it did (same headless-never-blocks invariant as every
    # other interactive gate in this codebase).
    architect_attempts: int
    architect_decision: str  # "skip" | "agent" | "retry" | "exhausted" | "approved" | "revise"

    # generic compile-fix re-entry (M4+): any phase that needs another pass
    # through the shared compile-fix subgraph sets phase="fix_cycle" and
    # fix_cycle_return_to to the node it wants control back at afterwards.
    # done/infra/halt route on `phase` alone (route_by_phase); after_fix_cycle
    # routes on `fix_cycle_return_to` alone — neither grows per phase.
    phase: str  # "slice" (default) | "fix_cycle" — selects done/infra/halt routing target
    fix_cycle_return_to: str  # node name after_fix_cycle proceeds to on non-fatal exit (e.g. "verify")

    # routes agent (M4)
    route_map: dict[str, Any] | None
    routes_attempts: int
    routes_decision: str  # "loop" | "noop" | "proceed" (routes_node's own routing signal)

    # config mapping agent (M4)
    config_map: dict[str, Any] | None
    config_mapping_attempts: int
    config_mapping_decision: str  # "loop" | "proceed" | "noop"

    # runtime wiring / boot verification (M4)
    boot_started: bool
    boot_log_tail: str
    runtime_wiring_attempts: int

    # human-in-the-loop gate on infra compile errors (M5): headless mode
    # always resolves to "abort"; interactive mode resolves to "retry" or
    # "abort" based on the human's interrupt() answer.
    human_gate_decision: str  # "retry" | "abort"

    # T2 signature-preservation tier (M6): accumulates across the whole run,
    # one entry per slice/whole-tree check (see tools/signature_diff.py).
    # Never gates the run by itself (soft finding, like routes/config_mapping) --
    # it records what a hollowed-out method looks like, it doesn't halt on one.
    signature_findings: list[dict[str, Any]]

    # Play-repo integrity guard (M6 Task 2): set by compile_node when
    # tools/play_guard.py reports anything other than "clean". Read by
    # route_after_compile to short-circuit straight to play_repo_tampered,
    # bypassing route_by_phase since this must abort regardless of phase.
    # MUST be declared here -- LangGraph's StateGraph(MigrationState) uses
    # this TypedDict as its schema and silently drops any node-returned key
    # that isn't declared, which is exactly the bug this comment is warning
    # the next person away from re-discovering the hard way.
    play_repo_guard_status: str  # "" (default/unset) | "tampered" | "error"

    # Findings severity model (M6 Task 10/11): tier/severity-tagged findings
    # from T4 (mvn test) and T5 (endpoint parity) -- matches the plugin's
    # qa_findings shape (tier, severity: blocker|major|minor, category,
    # scope, status). Distinct from signature_findings (T2, above) only
    # because T2 predates this general shape; a future pass could fold both
    # into one list, not done here to avoid touching Task 1's already-tested
    # accumulation logic.
    findings: list[dict[str, Any]]
    test_result: dict[str, Any] | None

    # T5 endpoint parity (M6 Task 10): status "not_attempted" | "error" |
    # "passed" | "differences_found". Never gates the run -- see
    # nodes/endpoint_parity.py.
    endpoint_verification: dict[str, Any] | None


# Exit-code parity with the legacy orchestrator (see migration_orchestrator.py:main)
EXIT_OK = 0
EXIT_STUCK_NO_LLM = 2
EXIT_INIT_NOT_DONE = 3
EXIT_BUDGET_EXHAUSTED = 4
EXIT_SLICE_FAILURE = 5
EXIT_INFRASTRUCTURE = 5
EXIT_NO_SLICES = 6

OUTCOME_EXIT_CODES: dict[str, int] = {
    "success": EXIT_OK,
    "failed": EXIT_OK,  # per-slice outcome; overall run exit code comes from run_exit_code
    "budget_exhausted": EXIT_BUDGET_EXHAUSTED,
    "timeout": EXIT_OK,
    "looping": EXIT_OK,
    "infrastructure_error": EXIT_INFRASTRUCTURE,
    "no_llm": EXIT_STUCK_NO_LLM,
}

# Run-level outcome -> exit code (legacy parity: the `return N` values in main()).
RUN_OUTCOME_EXIT_CODES: dict[str, int] = {
    "success": EXIT_OK,
    "slice_failures": EXIT_SLICE_FAILURE,
    "budget_exhausted": EXIT_BUDGET_EXHAUSTED,
    "infrastructure_error": EXIT_INFRASTRUCTURE,
    "no_llm": EXIT_STUCK_NO_LLM,
    "no_slices": EXIT_NO_SLICES,
    "setup_failed": 1,  # generic setup failure (jar build / setup.sh) — legacy parity: `return 1`
    "init_failed": EXIT_INIT_NOT_DONE,
    "runtime_wiring_failed": EXIT_SLICE_FAILURE,  # app never booted; same "manual intervention" bucket
    # M6 Task 2: the Play repo (read-only invariant) was modified during the
    # run, or the integrity guard itself could not run ("error" is treated
    # the same as "tampered" -- a guard that cannot run is not a guard that
    # passed). One of only two conditions that halt the whole run outright
    # rather than degrading to a soft/per-slice finding.
    "play_repo_tampered": EXIT_INFRASTRUCTURE,
}
