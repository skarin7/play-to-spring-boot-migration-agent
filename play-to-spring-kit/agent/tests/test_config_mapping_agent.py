"""agents/config_mapping.py unit tests: one config-mapping round with a fake model."""

from langchain_core.messages import AIMessage

from agent.agents.config_mapping import run_config_mapping_agent
from agent.config import AgentConfig


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
