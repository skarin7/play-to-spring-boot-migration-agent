"""agents/config_mapping.py unit tests: one config-mapping round with a fake model."""

from langchain_core.messages import AIMessage

from agent.agents.config_mapping import run_config_mapping_agent
from agent.config import AgentConfig, TaskSignals


class FakeToolModel:
    def __init__(self, responses):
        self.responses = list(responses)

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        return self.responses.pop(0)


LEFTOVER = {"app.secret": "changeme"}


def test_run_config_mapping_agent_writes_property_via_tools(tmp_path):
    cfg = AgentConfig(spring_repo=tmp_path)
    props_dir = tmp_path / "src" / "main" / "resources"
    props_dir.mkdir(parents=True)
    props_path = props_dir / "application.properties"
    props_path.write_text("app.secret=changeme\n", encoding="utf-8")

    model = FakeToolModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "str_replace",
                        "args": {
                            "path": "src/main/resources/application.properties",
                            "old": "app.secret=changeme\n",
                            "new": "app.secret=changeme\napp.security.secret=changeme\n",
                        },
                        "id": "1",
                    }
                ],
            ),
            AIMessage(content="done"),
        ]
    )
    edited, result = run_config_mapping_agent(cfg, LEFTOVER, attempt=1, model_override=model)
    assert len(edited) == 1
    assert "app.security.secret=changeme" in props_path.read_text(encoding="utf-8")
    assert result.tool_calls == 1
    usage_log = (cfg.migration_dir / "llm-usage.jsonl").read_text(encoding="utf-8")
    assert '"phase": "config_mapping"' in usage_log


def test_retry_count_and_leftover_count_signals_reach_choose_model(tmp_path):
    cfg = AgentConfig(spring_repo=tmp_path)
    props_dir = tmp_path / "src" / "main" / "resources"
    props_dir.mkdir(parents=True)
    (props_dir / "application.properties").write_text("a=1\nb=2\n", encoding="utf-8")

    seen_signals = []
    orig_choose_model = cfg.choose_model

    def spy(signals):
        seen_signals.append(signals)
        return orig_choose_model(signals)

    cfg.choose_model = spy
    model = FakeToolModel([AIMessage(content="done")])
    leftover = {"a": "1", "b": "2"}

    run_config_mapping_agent(cfg, leftover, attempt=3, model_override=model)

    assert seen_signals == [TaskSignals(retry_count=2, item_count=2)]
