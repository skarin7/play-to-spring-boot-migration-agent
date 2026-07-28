"""Engine configuration.

Env vars mirror the legacy orchestrator (MAX_TOTAL_LLM_CALLS,
MAX_RETRIES_PER_LAYER, TIMEOUT_PER_LAYER_MINS, ESCALATE_AFTER_RETRIES)
plus the OpenRouter backend settings.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

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

    # Setup phase (M3): toolkit build + kit setup.sh + optional conf export.
    toolkit_root: Path | None = None
    skip_build_toolkit: bool = False
    workspace_dir: Path | None = None
    spring_name: str | None = None
    export_play_conf: bool = False
    conf_strip_prefixes: list[str] = field(default_factory=list)

    # Bootstrap agent (M3)
    max_bootstrap_attempts: int = 2

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
