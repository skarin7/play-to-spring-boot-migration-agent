"""Config-mapping phase (M4 Task 2) — runs after the routes phase, before the
cross-module verify pass. Editing .properties values can never break
`mvn compile`, so unlike routes this phase never needs to re-enter the
compile-fix subgraph: it only self-loops or hands off to verify.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable

from ..agents.config_mapping import run_config_mapping_agent
from ..config import AgentConfig
from ..state import RUN_OUTCOME_EXIT_CODES, MigrationState
from ..status_v2 import atomic_write_json
from ..tools.config_mapping import append_properties, diff_config_keys, flatten_play_conf, read_properties_keys
from .common import phase_budget_decision

if TYPE_CHECKING:
    from ..graph import RuntimeCtx

LOG = logging.getLogger("agent.nodes.config_mapping")


def _write_config_map(config: AgentConfig, seed_mapped: dict[str, str], leftover: dict[str, str]) -> dict:
    config_map = {"seed_mapped": seed_mapped, "leftover": leftover}
    atomic_write_json(config.migration_dir / "config-map.json", config_map)
    return config_map


def route_after_config_mapping_node(state: MigrationState) -> str:
    if state.get("run_outcome"):
        return "halt"
    decision = state.get("config_mapping_decision")
    if decision == "loop":
        return "config_mapping"
    # Both "proceed" and "noop" land on verify — there's no routes-style
    # noop/proceed distinction needed downstream since config_mapping has
    # no fix-cycle to skip.
    return "verify"


def build(config: AgentConfig, ctx: "RuntimeCtx") -> dict[str, Callable]:
    def config_mapping_node(state: MigrationState) -> dict:
        attempts = state.get("config_mapping_attempts", 0)
        properties_path = config.spring_repo / "src" / "main" / "resources" / "application.properties"
        conf_path = (config.play_repo / "conf" / "application.conf") if config.play_repo is not None else None

        if attempts == 0:
            if (
                config.play_repo is None
                or not config.export_play_conf
                or conf_path is None
                or not conf_path.is_file()
                or not properties_path.is_file()
            ):
                # Nothing to map (no Play repo, conf export disabled, no
                # conf/application.conf, or the export never produced a
                # properties file to begin with) — no-op.
                config_map = _write_config_map(config, {}, {})
                return {"config_map": config_map, "config_mapping_decision": "noop"}

        # Recompute fresh every round: a previous agent round may have
        # resolved some (or all) of the previously-leftover keys, and the
        # seed pass below may append new canonical keys this round too.
        flattened = flatten_play_conf(conf_path)
        existing_keys = read_properties_keys(properties_path)
        seed_mapped, leftover = diff_config_keys(flattened, existing_keys)

        # Deterministic pass first, every round, regardless of attempts/budget
        # (zero LLM cost — same "determinism first" principle used throughout
        # this codebase; never gate this behind the agent or the attempts cap).
        if seed_mapped:
            append_properties(properties_path, seed_mapped)

        # Cumulative seed_mapped across rounds: each round's diff only
        # reflects keys not yet appended, so merge with what earlier rounds
        # already wrote to keep config-map.json a complete record. `leftover`
        # is always just this round's fresh diff (an agent round may have
        # resolved some of it already).
        cumulative_seed_mapped = dict((state.get("config_map") or {}).get("seed_mapped") or {})
        cumulative_seed_mapped.update(seed_mapped)

        if not leftover:
            config_map = _write_config_map(config, cumulative_seed_mapped, {})
            return {"config_map": config_map, "config_mapping_decision": "proceed"}

        # Shared three-way decision (budget checked before the per-phase
        # attempts cap — see phase_budget_decision) used by every bounded
        # per-phase LLM loop.
        decision = phase_budget_decision(state, config, attempts, config.max_config_mapping_attempts)
        if decision == "budget_exhausted":
            LOG.warning("config_mapping: global LLM budget exhausted, aborting run")
            config_map = _write_config_map(config, cumulative_seed_mapped, leftover)
            return {
                "config_map": config_map,
                "run_outcome": "budget_exhausted",
                "run_exit_code": RUN_OUTCOME_EXIT_CODES["budget_exhausted"],
            }
        if decision == "attempts_exhausted":
            LOG.warning(
                "config_mapping: giving up after %d attempts, %d keys still unmapped", attempts, len(leftover)
            )
            config_map = _write_config_map(config, cumulative_seed_mapped, leftover)
            return {"config_map": config_map, "config_mapping_decision": "proceed"}

        run_config_mapping_agent(config, leftover, attempt=attempts + 1, model_override=ctx.model_override)
        # Persist config_map on the loop branch too (not just the terminal
        # proceed/budget_exhausted paths) -- otherwise any seed_mapped keys
        # applied this round are silently lost from the audit record once a
        # later round's seed-diff comes up empty (they're already on disk,
        # so no longer "new" to re-report), even though config_map.json's
        # whole point is to be a complete cumulative record.
        config_map = _write_config_map(config, cumulative_seed_mapped, leftover)
        return {
            "config_map": config_map,
            "config_mapping_attempts": attempts + 1,
            "total_llm_calls": state.get("total_llm_calls", 0) + 1,
            "config_mapping_decision": "loop",
        }

    return {
        "config_mapping_node": config_mapping_node,
    }
