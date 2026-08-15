"""Setup + bootstrap nodes (M3)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from ..agents.bootstrap import run_bootstrap
from ..config import AgentConfig
from ..state import RUN_OUTCOME_EXIT_CODES, MigrationState

if TYPE_CHECKING:
    from ..graph import RuntimeCtx

LOG = logging.getLogger("agent.nodes.bootstrap")


def _bootstrap_files_present(spring_repo: Path) -> tuple[bool, bool, bool]:
    pom_ok = (spring_repo / "pom.xml").is_file()
    java_root = spring_repo / "src" / "main" / "java"
    app_ok = java_root.is_dir() and any(java_root.rglob("Application.java"))
    props_ok = (spring_repo / "src" / "main" / "resources" / "application.properties").is_file()
    return pom_ok, app_ok, props_ok


def route_after_setup(state: MigrationState) -> str:
    return "halt" if state.get("run_outcome") else "bootstrap_check"


def route_after_bootstrap_check(state: MigrationState) -> str:
    return "inventory" if state.get("bootstrap_decision") == "skip" else "bootstrap_agent"


def route_after_bootstrap_verify(state: MigrationState) -> str:
    return {"skip": "inventory", "retry": "bootstrap_agent", "exhausted": "run_halt"}[
        state.get("bootstrap_decision", "exhausted")
    ]


def build(config: AgentConfig, ctx: "RuntimeCtx") -> dict[str, Callable]:
    def setup_node(state: MigrationState) -> dict:
        if ctx.setup_ops is None:
            return {}
        for phase, op in (("jar", ctx.setup_ops.ensure_jar), ("setup.sh", ctx.setup_ops.install)):
            ok, msg = op(config)
            LOG.info("setup(%s): %s", phase, msg)
            if not ok:
                LOG.error("setup(%s) failed: %s", phase, msg)
                return {
                    "run_outcome": "setup_failed",
                    "run_exit_code": RUN_OUTCOME_EXIT_CODES["setup_failed"],
                }
        ok, msg = ctx.setup_ops.export_conf(config)
        LOG.info("setup(export-conf): %s", msg)
        return {}

    def bootstrap_check_node(state: MigrationState) -> dict:
        if config.play_repo is None:
            # No Play repo to scaffold from (standalone mode against an already
            # set-up Spring repo) — nothing for the bootstrap agent to do.
            return {"bootstrap_decision": "skip", "bootstrap_attempts": state.get("bootstrap_attempts", 0)}
        pom_ok, app_ok, props_ok = _bootstrap_files_present(config.spring_repo)
        decision = "skip" if (pom_ok and app_ok and props_ok) else "agent"
        return {"bootstrap_decision": decision, "bootstrap_attempts": state.get("bootstrap_attempts", 0)}

    def bootstrap_agent_node(state: MigrationState) -> dict:
        attempt = state.get("bootstrap_attempts", 0) + 1
        run_bootstrap(config, attempt, model_override=ctx.bootstrap_model_override)
        return {"bootstrap_attempts": attempt}

    def bootstrap_verify_node(state: MigrationState) -> dict:
        pom_ok, app_ok, props_ok = _bootstrap_files_present(config.spring_repo)
        if pom_ok and app_ok and props_ok:
            return {"bootstrap_decision": "skip"}
        if state.get("bootstrap_attempts", 0) >= config.max_bootstrap_attempts:
            LOG.error(
                "bootstrap: giving up after %d attempts (pom=%s app=%s props=%s)",
                state.get("bootstrap_attempts", 0), pom_ok, app_ok, props_ok,
            )
            return {
                "bootstrap_decision": "exhausted",
                "run_outcome": "init_failed",
                "run_exit_code": RUN_OUTCOME_EXIT_CODES["init_failed"],
            }
        return {"bootstrap_decision": "retry"}

    return {
        "setup_node": setup_node,
        "bootstrap_check_node": bootstrap_check_node,
        "bootstrap_agent_node": bootstrap_agent_node,
        "bootstrap_verify_node": bootstrap_verify_node,
    }
