"""CLI entry point: ``python -m agent --spring-repo <path>``.

M1 scope: run the compile-fix loop against an existing Spring repo until
green, budget exhausted, or stuck. Resumable via the sqlite checkpointer.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from langgraph.errors import GraphRecursionError
from langgraph.types import Command

from .checkpoint import make_checkpointer, thread_id_for
from .config import AgentConfig
from .graph import build_graph, recursion_limit
from .report import console_summary, report_only, write_report
from .state import MigrationState
from .status_v2 import status_v2_to_state, write_status_v2

# Conventional "process interrupted before completing" code (matches the
# shell's 128+SIGINT=130 convention). Used when the CLI can't get a
# retry/abort answer from a human (stdin closed or Ctrl-C at the prompt) --
# the run itself is untouched and safely resumable under the same
# thread_id, this is NOT a run failure, so it deliberately isn't one of the
# OUTCOME_EXIT_CODES/RUN_OUTCOME_EXIT_CODES values in state.py.
EXIT_AWAITING_HUMAN_INPUT = 130

_HUMAN_GATE_LOG_TAIL_CHARS = 800


def _print_interrupt(itr) -> None:
    """Print an interrupt payload for a human to actually read: real
    newlines in the log tail, not one long dict-repr line with literal
    '\\n' escapes."""
    payload = itr.value
    if not isinstance(payload, dict):
        print(f"\n[human-gate] {payload}", file=sys.stderr)
        return
    print(
        f"\n[human-gate] reason={payload.get('reason')} slice_id={payload.get('slice_id')}",
        file=sys.stderr,
    )
    log_tail = (payload.get("log_tail") or "")[-_HUMAN_GATE_LOG_TAIL_CHARS:]
    if log_tail:
        print("----- compile log (tail) -----", file=sys.stderr)
        print(log_tail, file=sys.stderr)
        print("----- end compile log -----", file=sys.stderr)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="agent", description="LangGraph migration engine")
    p.add_argument("--spring-repo", required=True, type=Path)
    p.add_argument("--play-repo", type=Path, default=None)
    p.add_argument("--slice-id", default="default")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--fresh", action="store_true", help="ignore existing checkpoint")
    p.add_argument("--verbose", "-v", action="store_true")
    p.add_argument(
        "--interactive",
        action="store_true",
        help="pause on infrastructure errors for a human decision instead of failing immediately",
    )
    p.add_argument(
        "--report-only",
        action="store_true",
        help="regenerate .migration/report.html from an existing migration-status.json "
        "and exit -- does not run the graph or touch migration state",
    )

    # Setup phase (M3): toolkit build + kit setup.sh + optional conf export.
    p.add_argument("--workspace", type=Path, default=None, help="default: parent of --play-repo")
    p.add_argument("--spring-name", default=None)
    p.add_argument("--toolkit-root", type=Path, default=None)
    p.add_argument(
        "--skip-build-toolkit",
        action="store_true",
        help="require an existing JAR in play-to-spring-kit/lib/ instead of fetching/building it",
    )
    p.add_argument(
        "--build-toolkit-from-source",
        action="store_true",
        help="build java-dev-toolkit with mvn instead of fetching the pinned release jar "
        "(for toolkit developers testing unreleased changes)",
    )
    p.add_argument("--export-play-conf", action="store_true")
    p.add_argument("--conf-strip-prefix", action="append", default=[], metavar="PREFIX")
    p.add_argument("--max-bootstrap-attempts", type=int, default=2)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    config = AgentConfig(
        spring_repo=args.spring_repo,
        play_repo=args.play_repo,
        dry_run=args.dry_run,
        workspace_dir=args.workspace,
        spring_name=args.spring_name,
        toolkit_root=args.toolkit_root,
        skip_build_toolkit=args.skip_build_toolkit,
        build_toolkit_from_source=args.build_toolkit_from_source,
        export_play_conf=args.export_play_conf,
        conf_strip_prefixes=list(args.conf_strip_prefix or []),
        max_bootstrap_attempts=args.max_bootstrap_attempts,
        headless=not args.interactive,
    )
    if not config.spring_repo.is_dir():
        print(f"error: spring repo not found: {config.spring_repo}", file=sys.stderr)
        return 1

    if args.report_only:
        # Does not run the graph or touch migration state -- reads
        # migration-status.json straight off disk (see report.py:report_only).
        try:
            path = report_only(config)
        except FileNotFoundError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"wrote {path}", file=sys.stderr)
        return 0

    if not config.api_key:
        print(
            "warning: OPENROUTER_API_KEY not set — LLM fix rounds disabled "
            "(deterministic fixers still run)",
            file=sys.stderr,
        )

    checkpointer = make_checkpointer(config)
    graph = build_graph(config).compile(checkpointer=checkpointer)

    thread_id = thread_id_for(config)
    if args.fresh:
        thread_id = f"{thread_id}-{args.slice_id}-fresh"

    initial: MigrationState = {
        "spring_repo": str(config.spring_repo),
        "slice_id": args.slice_id,
        "retry_count": 0,
        "total_llm_calls": 0,
    }
    run_config = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": recursion_limit(config),
    }
    if config.tracing_enabled:
        # Threaded through as run-level tags/metadata (M6 Task 12) rather
        # than left to LangSmith's own per-call defaults, so every LLM call
        # inside this graph.invoke -- and a resumed run's later invoke
        # calls, since thread_id is stable across resumes -- groups under
        # one identity in the LangSmith UI. LANGCHAIN_TRACING_V2/
        # LANGCHAIN_API_KEY (read by LangChain itself, not this code) are
        # what actually turn export on; config.tracing_enabled only gates
        # whether THIS engine adds these extra tags on top.
        run_config["tags"] = [f"thread:{thread_id}", f"spring_repo:{config.spring_repo.name}"]
        run_config["metadata"] = {
            "project_name": config.trace_project,
            "thread_id": thread_id,
            "spring_repo": str(config.spring_repo),
        }

    # One get_state call serves two purposes below: (1) adoption eligibility
    # -- an empty .values means no langgraph checkpoint has ever been
    # written for this thread, so a legacy migration-status.json (if any) is
    # safe to adopt into `initial`; (2) resume-vs-fresh-invoke -- a non-empty
    # .next means this thread is currently paused at human_gate's
    # interrupt(), so we must resume it rather than starting over (see the
    # comment at the invoke call below).
    existing = graph.get_state(run_config)

    if not args.fresh and not existing.values and config.status_path and config.status_path.is_file():
        try:
            raw_status = json.loads(config.status_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"warning: could not read {config.status_path} for adoption: {exc}", file=sys.stderr)
            raw_status = None
        if isinstance(raw_status, dict):
            adopted = status_v2_to_state(raw_status)
            units = adopted.get("migration_units")
            if units:
                done = sum(1 for u in units if u.get("status") == "done")
                print(
                    f"adopting legacy status from {config.status_path}: "
                    f"{done}/{len(units)} units already done, "
                    f"total_llm_calls={adopted.get('total_llm_calls', 0)}",
                    file=sys.stderr,
                )
                initial.update(adopted)

    try:
        # A previous --interactive run may have left this exact thread paused
        # at human_gate's interrupt() (process killed, terminal closed, etc).
        # graph.invoke(initial, ...) on an already-paused thread does NOT
        # resume it -- it silently discards the pending paused task and
        # restarts the ENTIRE graph from START with fresh state (re-running
        # bootstrap/inventory/slice-pipeline/compile), throwing away the
        # human's pending decision and burning real compute. existing.next
        # (non-empty tuple == paused, matches both InMemorySaver and the
        # real SqliteSaver used here) tells us to resume via invoke(None,
        # ...) instead, which re-poses the same interrupt without
        # re-executing anything upstream of it.
        if existing.next:
            final = graph.invoke(None, config=run_config)
        else:
            final = graph.invoke(initial, config=run_config)

        while "__interrupt__" in final:
            for itr in final["__interrupt__"]:
                _print_interrupt(itr)
            try:
                answer = input(
                    "Compile hit an infrastructure error. Retry, or accept as a hard failure? [retry/abort]: "
                )
            except (EOFError, KeyboardInterrupt):
                print(
                    f"\nrun is paused at an infrastructure-error decision and is safely "
                    f"resumable -- rerun the same command to continue (thread_id={thread_id})",
                    file=sys.stderr,
                )
                return EXIT_AWAITING_HUMAN_INPUT
            normalized = answer.strip().lower()
            if normalized not in ("retry", "abort"):
                print(f"unrecognized input '{answer}', treating as abort", file=sys.stderr)
            final = graph.invoke(Command(resume=answer), config=run_config)
    except GraphRecursionError:
        # Backstop only: guards (budget/retries/timeout/loop detection) should
        # always halt before this fires. Fail closed with a defined exit code
        # instead of an unhandled traceback; the run is resumable under the
        # same thread_id once the underlying guard gap is fixed or budgets
        # are tightened.
        print(
            f"error: recursion limit ({recursion_limit(config)}) reached without a "
            f"guard halting the run — this indicates a guard bug, not a normal "
            f"outcome (thread_id={thread_id})",
            file=sys.stderr,
        )
        return 1

    write_status_v2(final, config)
    write_report(final, config)

    outcome = final.get("run_outcome", "failed")
    exit_code = int(final.get("run_exit_code", 1))
    units = final.get("migration_units") or []
    done = sum(1 for u in units if u.get("status") == "done")
    print(
        f"run_outcome={outcome} slices_done={done}/{len(units)} "
        f"llm_calls={final.get('total_llm_calls', 0)} exit={exit_code}"
    )
    # M6 Task 11: blocker-severity findings only + cost + a pointer to the
    # full report -- everything else (clean layers, major/minor findings)
    # lives in report.html, not the console.
    print(console_summary(final, config))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
