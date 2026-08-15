"""Tracing config + run_tool_loop wiring tests (M6 Task 12): tracing_enabled
reads LANGCHAIN_TRACING_V2, and gates whether bound.invoke gets a config=
kwarg with tags/metadata at all -- an untraced run must build nothing extra.
"""

from langchain_core.messages import AIMessage

from agent.config import AgentConfig
from agent.llm import run_tool_loop


def make_config(tmp_path, **kw) -> AgentConfig:
    return AgentConfig(spring_repo=tmp_path, **kw)


# ----------------------------------------------------------------------
# config.tracing_enabled / trace_project
# ----------------------------------------------------------------------


def test_tracing_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    cfg = make_config(tmp_path)
    assert cfg.tracing_enabled is False


def test_tracing_enabled_via_env(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    cfg = make_config(tmp_path)
    assert cfg.tracing_enabled is True


def test_tracing_env_case_insensitive(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "TRUE")
    cfg = make_config(tmp_path)
    assert cfg.tracing_enabled is True


def test_tracing_env_other_value_is_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "yes")  # not the literal "true"
    cfg = make_config(tmp_path)
    assert cfg.tracing_enabled is False


def test_trace_project_default(tmp_path, monkeypatch):
    monkeypatch.delenv("LANGCHAIN_PROJECT", raising=False)
    cfg = make_config(tmp_path)
    assert cfg.trace_project == "play-to-spring-migration"


def test_trace_project_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGCHAIN_PROJECT", "my-custom-project")
    cfg = make_config(tmp_path)
    assert cfg.trace_project == "my-custom-project"


# ----------------------------------------------------------------------
# run_tool_loop: invoke_kwargs only built when tracing_enabled
# ----------------------------------------------------------------------


class _CapturingModel:
    def __init__(self):
        self.invoke_calls: list[tuple[tuple, dict]] = []

    def bind_tools(self, tools):
        return self

    def invoke(self, messages, **kwargs):
        self.invoke_calls.append((messages, kwargs))
        return AIMessage(content="done", usage_metadata={"input_tokens": 5, "output_tokens": 1, "total_tokens": 6})


def test_tracing_disabled_invoke_receives_no_config_kwarg(tmp_path, monkeypatch):
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    cfg = make_config(tmp_path)
    model = _CapturingModel()

    run_tool_loop(model=model, tools=[], system="sys", user="task", max_tool_calls=5, config=cfg, phase="compile_fix")

    _messages, kwargs = model.invoke_calls[0]
    assert kwargs == {}


def test_tracing_enabled_invoke_receives_tags_and_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    cfg = make_config(tmp_path)
    model = _CapturingModel()

    run_tool_loop(model=model, tools=[], system="sys", user="task", max_tool_calls=5, config=cfg, phase="compile_fix")

    _messages, kwargs = model.invoke_calls[0]
    assert "config" in kwargs
    assert "phase:compile_fix" in kwargs["config"]["tags"]
    assert kwargs["config"]["metadata"]["phase"] == "compile_fix"
    assert "request_id" in kwargs["config"]["metadata"]


def test_tracing_enabled_run_name_includes_round_number(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    cfg = make_config(tmp_path)
    model = _CapturingModel()

    run_tool_loop(model=model, tools=[], system="sys", user="task", max_tool_calls=5, config=cfg, phase="routes")

    _messages, kwargs = model.invoke_calls[0]
    assert kwargs["config"]["run_name"] == "routes-round-1"


def test_tracing_metadata_request_id_consistent_across_rounds(tmp_path, monkeypatch):
    """One tool loop == one request_id, even across multiple rounds --
    that's what lets a LangSmith trace be correlated back to one
    llm-debug.jsonl request_id group."""
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    cfg = make_config(tmp_path)

    class TwoRoundModel(_CapturingModel):
        def invoke(self, messages, **kwargs):
            self.invoke_calls.append((messages, kwargs))
            if len(self.invoke_calls) == 1:
                return AIMessage(
                    content="",
                    tool_calls=[{"name": "noop", "args": {}, "id": "1"}],
                    usage_metadata={"input_tokens": 5, "output_tokens": 1, "total_tokens": 6},
                )
            return AIMessage(content="done", usage_metadata={"input_tokens": 5, "output_tokens": 1, "total_tokens": 6})

    model = TwoRoundModel()
    run_tool_loop(model=model, tools=[], system="sys", user="task", max_tool_calls=5, config=cfg, phase="compile_fix")

    assert len(model.invoke_calls) == 2
    request_ids = {kwargs["config"]["metadata"]["request_id"] for _m, kwargs in model.invoke_calls}
    assert len(request_ids) == 1  # same request_id across both rounds
