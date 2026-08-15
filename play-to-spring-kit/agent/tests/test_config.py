"""config.py:AgentConfig.max_agent_tool_calls_for tests (M6 Task 9): per-phase
tool-call cap, default unchanged unless explicitly tuned via env override."""

from pathlib import Path

from agent.config import AgentConfig


def make_config(tmp_path: Path, **kw) -> AgentConfig:
    return AgentConfig(spring_repo=tmp_path, **kw)


def test_defaults_to_global_cap_when_no_override(tmp_path):
    cfg = make_config(tmp_path, max_agent_tool_calls=8)
    assert cfg.max_agent_tool_calls_for("compile_fix") == 8
    assert cfg.max_agent_tool_calls_for("routes") == 8


def test_env_override_for_one_phase_does_not_affect_others(tmp_path, monkeypatch):
    monkeypatch.setenv("MAX_AGENT_TOOL_CALLS_COMPILE_FIX", "20")
    cfg = make_config(tmp_path, max_agent_tool_calls=8)

    assert cfg.max_agent_tool_calls_for("compile_fix") == 20
    assert cfg.max_agent_tool_calls_for("routes") == 8


def test_env_override_case_insensitive_phase_name(tmp_path, monkeypatch):
    monkeypatch.setenv("MAX_AGENT_TOOL_CALLS_RUNTIME_WIRING", "15")
    cfg = make_config(tmp_path)
    assert cfg.max_agent_tool_calls_for("runtime_wiring") == 15


def test_unset_env_falls_back_to_global(tmp_path, monkeypatch):
    monkeypatch.delenv("MAX_AGENT_TOOL_CALLS_ARCHITECT", raising=False)
    cfg = make_config(tmp_path, max_agent_tool_calls=8)
    assert cfg.max_agent_tool_calls_for("architect") == 8
