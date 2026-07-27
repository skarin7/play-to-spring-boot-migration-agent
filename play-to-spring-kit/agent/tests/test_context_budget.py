"""Context-window budgeting for run_tool_loop (M5): config, compact_messages,
and the headless/interactive trigger wiring in agent.llm.run_tool_loop."""

from agent.config import AgentConfig


def test_max_agent_context_tokens_default(tmp_path):
    cfg = AgentConfig(spring_repo=tmp_path)
    assert cfg.max_agent_context_tokens == 50_000


def test_max_agent_context_tokens_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("MAX_AGENT_CONTEXT_TOKENS", "12345")
    cfg = AgentConfig(spring_repo=tmp_path)
    assert cfg.max_agent_context_tokens == 12345
