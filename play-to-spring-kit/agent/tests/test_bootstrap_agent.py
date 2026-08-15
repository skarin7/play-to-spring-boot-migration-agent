"""agents/bootstrap.py tests: skill extraction + one bootstrap round with a fake model."""

from langchain_core.messages import AIMessage

from agent.agents.bootstrap import extract_builder_skill_step1_only, run_bootstrap
from agent.config import AgentConfig


class FakeToolModel:
    def __init__(self, responses):
        self.responses = list(responses)

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        return self.responses.pop(0)


def test_extract_builder_skill_step1_only():
    md = "intro\n## Step 1: Initialize Spring project\nbody1\n\n## Step 2: Compile\nbody2\n"
    chunk = extract_builder_skill_step1_only(md)
    assert chunk.startswith("## Step 1: Initialize Spring project")
    assert "body1" in chunk
    assert "Step 2" not in chunk


def test_extract_builder_skill_step1_only_missing_heading():
    assert extract_builder_skill_step1_only("no headings here") is None


def test_run_bootstrap_creates_files_via_tools(tmp_path):
    cfg = AgentConfig(spring_repo=tmp_path)
    model = FakeToolModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "write_file", "args": {"path": "pom.xml", "content": "<project/>"}, "id": "1"},
                    {
                        "name": "write_file",
                        "args": {
                            "path": "src/main/java/com/example/Application.java",
                            "content": "class Application {}",
                        },
                        "id": "2",
                    },
                ],
            ),
            AIMessage(content="done"),
        ]
    )
    edited, result = run_bootstrap(cfg, attempt=1, model_override=model)
    assert (tmp_path / "pom.xml").is_file()
    assert (tmp_path / "src/main/java/com/example/Application.java").is_file()
    assert len(edited) == 2
    assert result.tool_calls == 2
