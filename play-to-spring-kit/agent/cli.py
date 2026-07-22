"""CLI entry point: ``python -m agent --spring-repo <path>``.

M1 scope: run the compile-fix loop against an existing Spring repo until
green, budget exhausted, or stuck. Resumable via the sqlite checkpointer.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from langgraph.errors import GraphRecursionError
from langgraph.types import Command

from .checkpoint import make_checkpointer, thread_id_for
from .config import AgentConfig
from .graph import build_graph, recursion_limit
from .state import MigrationState
from .status_v2 import write_status_v2


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

    # Setup phase (M3): toolkit build + kit setup.sh + optional conf export.
    p.add_argument("--workspace", type=Path, default=None, help="default: parent of --play-repo")
    p.add_argument("--spring-name", default=None)
    p.add_argument("--toolkit-root", type=Path, default=None)
    p.add_argument(
        "--skip-build-toolkit",
        action="store_true",
        help="require an existing JAR in play-to-spring-kit/lib/ instead of building it",
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
        export_play_conf=args.export_play_conf,
        conf_strip_prefixes=list(args.conf_strip_prefix or []),
        max_bootstrap_attempts=args.max_bootstrap_attempts,
        headless=not args.interactive,
    )
    if not config.spring_repo.is_dir():
        print(f"error: spring repo not found: {config.spring_repo}", file=sys.stderr)
        return 1
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

    try:
        final = graph.invoke(initial, config=run_config)
        while "__interrupt__" in final:
            for itr in final["__interrupt__"]:
                print(f"\n[human-gate] {itr.value}", file=sys.stderr)
            answer = input(
                "Compile hit an infrastructure error. Retry, or accept as a hard failure? [retry/abort]: "
            )
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

    outcome = final.get("run_outcome", "failed")
    exit_code = int(final.get("run_exit_code", 1))
    units = final.get("migration_units") or []
    done = sum(1 for u in units if u.get("status") == "done")
    print(
        f"run_outcome={outcome} slices_done={done}/{len(units)} "
        f"llm_calls={final.get('total_llm_calls', 0)} exit={exit_code}"
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
