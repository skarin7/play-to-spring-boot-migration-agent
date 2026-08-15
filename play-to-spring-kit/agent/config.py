"""Engine configuration.

Env vars mirror the legacy orchestrator (MAX_TOTAL_LLM_CALLS,
MAX_RETRIES_PER_LAYER, TIMEOUT_PER_LAYER_MINS, ESCALATE_AFTER_RETRIES)
plus the OpenRouter backend settings.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

# Loaded once at import time, before any AgentConfig() reads os.environ.
# find_dotenv() walks up from cwd, so this picks up the repo-root .env
# regardless of whether you run from repo root or play-to-spring-kit/.
# Vars already set in the real environment are never overridden (override=False).
load_dotenv(find_dotenv(usecwd=True))

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL_CHEAP = "anthropic/claude-haiku-4.5"
DEFAULT_MODEL_PREMIUM = "anthropic/claude-sonnet-4.5"


def _env_int(name: str, default: int) -> int:
    v = os.environ.get(name, "").strip()
    return int(v) if v else default


@dataclass
class AgentConfig:
    spring_repo: Path
    play_repo: Path | None = None
    status_path: Path | None = None
    jar_path: Path | None = None  # dev-toolkit JAR; default: <play_repo>/dev-toolkit-1.0.0.jar
    migrate_batch_size: int | None = None
    headless: bool = True
    dry_run: bool = False

    # Setup phase (M3): toolkit jar + kit setup.sh + optional conf export.
    toolkit_root: Path | None = None
    skip_build_toolkit: bool = False
    build_toolkit_from_source: bool = False
    workspace_dir: Path | None = None
    spring_name: str | None = None
    export_play_conf: bool = False
    conf_strip_prefixes: list[str] = field(default_factory=list)

    # Bootstrap agent (M3)
    max_bootstrap_attempts: int = 2

    # Architect agent (M6 Task 6)
    max_architect_attempts: int = 2

    # Routes agent (M4)
    max_routes_attempts: int = 2

    # Config-mapping agent (M4)
    max_config_mapping_attempts: int = 2

    # Runtime-wiring agent / boot verification (M4). Worst case is a wall-clock
    # lower bound, not counting each retry's own LLM tool-loop time: 6 attempts
    # x 90s boot_timeout_sec = 9 minutes of boot-waiting alone, plus up to
    # max_agent_tool_calls tool round-trips per attempt (premium tier for the
    # last 4 attempts once escalate_after_retries=2 is exceeded).
    max_runtime_wiring_attempts: int = 6
    boot_timeout_sec: int = 90

    # T4 tests (M6 Task 10): `mvn test` in verify_node, gated on this flag --
    # default on. A test failure is a finding, never a halt (same soft-finding
    # model as T2 signatures and routes/config_mapping).
    run_tests: bool = field(default_factory=lambda: os.environ.get("MIGRATION_RUN_TESTS", "1").strip() != "0")
    test_timeout_sec: int = field(default_factory=lambda: _env_int("MIGRATION_TEST_TIMEOUT_SEC", 600))

    # Play-repo integrity guard (M6). Detection layer for the read-only-Play
    # invariant that FsJail only enforces for LLM writes -- setup.sh and the
    # dev-toolkit JAR both run with cwd=play_repo, unjailed. On by default
    # whenever play_repo is configured; the escape hatch exists for
    # standalone/no-play_repo test setups where there is nothing to guard.
    play_guard_enabled: bool = field(
        default_factory=lambda: os.environ.get("MIGRATION_PLAY_GUARD_ENABLED", "1").strip() != "0"
    )

    # LLM backend (OpenRouter / any OpenAI-compatible endpoint)
    base_url: str = field(
        default_factory=lambda: os.environ.get("OPENROUTER_BASE_URL", DEFAULT_BASE_URL)
    )
    api_key: str | None = field(
        default_factory=lambda: os.environ.get("OPENROUTER_API_KEY") or None
    )
    model_cheap: str = field(
        default_factory=lambda: os.environ.get("MIGRATION_MODEL_CHEAP", DEFAULT_MODEL_CHEAP)
    )
    model_premium: str = field(
        default_factory=lambda: os.environ.get("MIGRATION_MODEL_PREMIUM", DEFAULT_MODEL_PREMIUM)
    )
    llm_timeout_sec: int = field(default_factory=lambda: _env_int("LLM_TIMEOUT_SEC", 600))

    # Guardrails (parity with legacy Guardrails dataclass)
    max_total_llm_calls: int = field(default_factory=lambda: _env_int("MAX_TOTAL_LLM_CALLS", 50))
    # M6 Task 5: a dollar cap alongside the call-count cap. 0 (default) means
    # off -- call count alone is the pre-existing behavior, unchanged unless
    # this is explicitly set. Checked with the SAME precedence as
    # max_total_llm_calls: before any phase-local attempts cap, aborting the
    # whole run (see guards.decide and nodes/common.phase_budget_decision).
    max_total_cost_usd: float = field(
        default_factory=lambda: float(os.environ.get("MAX_TOTAL_COST_USD", "0") or "0")
    )
    max_retries_per_layer: int = field(
        default_factory=lambda: _env_int("MAX_RETRIES_PER_LAYER", 5)
    )
    timeout_layer_mins: int = field(default_factory=lambda: _env_int("TIMEOUT_PER_LAYER_MINS", 30))
    escalate_after_retries: int = field(
        default_factory=lambda: _env_int("ESCALATE_AFTER_RETRIES", 2)
    )
    escalate_item_threshold: int = field(
        default_factory=lambda: _env_int("MIGRATION_ESCALATE_ITEM_THRESHOLD", 5)
    )
    max_agent_tool_calls: int = field(default_factory=lambda: _env_int("MAX_AGENT_TOOL_CALLS", 8))
    max_agent_context_tokens: int = field(
        default_factory=lambda: _env_int("MAX_AGENT_CONTEXT_TOKENS", 50_000)
    )

    # Prompt caching (M6 Task 4): marks the system prompt + the stable prefix
    # of the user message as an Anthropic cache_control breakpoint, routed
    # through OpenRouter. Default on; an escape hatch for a routed model that
    # rejects cache_control blocks rather than ignoring them gracefully --
    # OpenRouter model support varies (see docs/superpowers/plans/
    # 2026-08-15-plugin-parity-hardening.md, Task 4 open question).
    prompt_caching_enabled: bool = field(
        default_factory=lambda: os.environ.get("MIGRATION_PROMPT_CACHING", "1").strip() != "0"
    )

    # Tracing (M6 Task 12): LangSmith export for every LangChain/LangGraph
    # call is itself entirely env-var driven (LANGCHAIN_TRACING_V2,
    # LANGCHAIN_API_KEY) -- no code wiring needed to turn it on, which is
    # the "near-free" the plan refers to. What this engine adds on top: a
    # per-run trace_id (so every LLM call and the graph.invoke that contains
    # them group under one identity in the LangSmith UI) and a stable
    # project name, both threaded through as run-level tags/metadata by
    # cli.py's run_config rather than left to LangSmith's own defaults.
    tracing_enabled: bool = field(
        default_factory=lambda: os.environ.get("LANGCHAIN_TRACING_V2", "").strip().lower() == "true"
    )
    trace_project: str = field(
        default_factory=lambda: os.environ.get("LANGCHAIN_PROJECT", "play-to-spring-migration")
    )

    def max_agent_tool_calls_for(self, phase: str) -> int:
        """Per-phase tool-call cap (M6 Task 9), env override
        MAX_AGENT_TOOL_CALLS_<PHASE> (e.g. MAX_AGENT_TOOL_CALLS_COMPILE_FIX).
        Defaults to max_agent_tool_calls unchanged, so nothing changes for a
        phase unless explicitly tuned. Reads the env fresh on each call
        (like every other _env_int use) rather than caching at __post_init__
        time, so a test's monkeypatch.setenv takes effect without needing a
        fresh AgentConfig."""
        env_name = f"MAX_AGENT_TOOL_CALLS_{phase.upper()}"
        return _env_int(env_name, self.max_agent_tool_calls)

    def __post_init__(self) -> None:
        self.spring_repo = Path(self.spring_repo).resolve()
        if self.play_repo is not None:
            self.play_repo = Path(self.play_repo).resolve()
        if self.status_path is None:
            self.status_path = self.spring_repo / "migration-status.json"
        if self.jar_path is None and self.play_repo is not None:
            self.jar_path = self.play_repo / "dev-toolkit-1.0.0.jar"
        if self.workspace_dir is None and self.play_repo is not None:
            self.workspace_dir = self.play_repo.parent
        elif self.workspace_dir is not None:
            self.workspace_dir = Path(self.workspace_dir).resolve()

    @property
    def migration_dir(self) -> Path:
        return self.spring_repo / ".migration"

    def choose_model(self, signals: "TaskSignals") -> str:
        """Two-tier heuristic routing: escalate to model_premium when either
        the retry count or the task's item_count (phase-specific magnitude of
        remaining work -- error clusters, unmapped routes, leftover config
        keys, boot-failure count) crosses its threshold."""
        if signals.retry_count >= self.escalate_after_retries:
            return self.model_premium
        if signals.item_count >= self.escalate_item_threshold:
            return self.model_premium
        return self.model_cheap


@dataclass
class TaskSignals:
    """Complexity signals fed to AgentConfig.choose_model. item_count's
    meaning is phase-specific (error clusters, unmapped routes, leftover
    config keys, boot-failure count) -- see docs/superpowers/specs/
    2026-07-27-heuristic-model-router-design.md."""

    retry_count: int = 0
    item_count: int = 0
