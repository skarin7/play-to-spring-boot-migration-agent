"""agents/routes.py unit tests: one routes-mapping round with a fake model."""

from langchain_core.messages import AIMessage

from agent.agents.routes import run_routes_agent
from agent.config import AgentConfig


class FakeToolModel:
    def __init__(self, responses):
        self.responses = list(responses)

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        return self.responses.pop(0)


UNMAPPED = [
    {"method": "GET", "path": "/users", "controller": "controllers.UserController", "action": "list", "params": ""},
]


def test_run_routes_agent_writes_annotation_via_tools(tmp_path):
    cfg = AgentConfig(spring_repo=tmp_path)
    java_dir = tmp_path / "src" / "main" / "java" / "controllers"
    java_dir.mkdir(parents=True)
    java_file = java_dir / "UserController.java"
    java_file.write_text(
        "package controllers;\n\n"
        "@RestController\n"
        "public class UserController {\n"
        "    public ResponseEntity<List<User>> list() {\n"
        "        return null;\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    model = FakeToolModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "str_replace",
                        "args": {
                            "path": "src/main/java/controllers/UserController.java",
                            "old": "    public ResponseEntity<List<User>> list() {",
                            "new": '    @GetMapping("/users")\n    public ResponseEntity<List<User>> list() {',
                        },
                        "id": "1",
                    }
                ],
            ),
            AIMessage(content="done"),
        ]
    )
    edited, result = run_routes_agent(cfg, UNMAPPED, attempt=1, model_override=model)
    assert len(edited) == 1
    assert '@GetMapping("/users")' in java_file.read_text(encoding="utf-8")
    assert result.tool_calls == 1
    usage_log = (cfg.migration_dir / "llm-usage.jsonl").read_text(encoding="utf-8")
    assert '"phase": "routes"' in usage_log
