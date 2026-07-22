"""agents/runtime_wiring.py unit tests: one runtime-wiring round with a fake
model, the reference-doc heading slice, and tier escalation (cheap x2 then
premium, via config.model_for_retry -- unlike routes/config_mapping which
are cheap-only)."""

from langchain_core.messages import AIMessage

from agent.agents.runtime_wiring import load_runtime_wiring_reference, run_runtime_wiring_agent
from agent.config import AgentConfig


class FakeToolModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.bound_with = None

    def bind_tools(self, tools):
        self.bound_with = tools
        return self

    def invoke(self, messages):
        return self.responses.pop(0)


def test_run_runtime_wiring_agent_writes_fix_via_tools(tmp_path):
    cfg = AgentConfig(spring_repo=tmp_path)
    java_dir = tmp_path / "src" / "main" / "java" / "config"
    java_dir.mkdir(parents=True)
    java_file = java_dir / "RestTemplateConfig.java"
    java_file.write_text("package config;\n\npublic class RestTemplateConfig {\n}\n", encoding="utf-8")

    model = FakeToolModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "str_replace",
                        "args": {
                            "path": "src/main/java/config/RestTemplateConfig.java",
                            "old": "public class RestTemplateConfig {",
                            "new": (
                                "@Configuration\npublic class RestTemplateConfig {\n"
                                "    @Bean\n    RestTemplate restTemplate() { return new RestTemplate(); }"
                            ),
                        },
                        "id": "1",
                    }
                ],
            ),
            AIMessage(content="done"),
        ]
    )
    edited, result = run_runtime_wiring_agent(
        cfg, "RestTemplate bean not found", attempt=1, model_override=model
    )
    assert len(edited) == 1
    assert "@Bean" in java_file.read_text(encoding="utf-8")
    assert result.tool_calls == 1
    usage_log = (cfg.migration_dir / "llm-usage.jsonl").read_text(encoding="utf-8")
    assert '"phase": "runtime_wiring"' in usage_log


def test_load_runtime_wiring_reference_slices_just_that_section():
    ref = load_runtime_wiring_reference()
    assert "Spring Boot runtime" in ref
    assert "RestTemplate` bean not found" in ref
    # Must not spill into the next `##` section.
    assert "Still manual or follow-up toolkit work" not in ref
    # Must not include the earlier, unrelated `##` sections either.
    assert "Automated in java-dev-toolkit" not in ref


def test_tier_escalates_cheap_then_premium_after_escalate_after_retries(tmp_path):
    """attempt N maps to retry_count N-1 (agent_node's 0-indexed convention):
    with the default escalate_after_retries=2, attempts 1-2 are cheap,
    attempt 3+ is premium."""
    cfg = AgentConfig(spring_repo=tmp_path)
    seen_models: list[str] = []
    orig_model_for_retry = cfg.model_for_retry

    def spy(retry_count):
        model_name = orig_model_for_retry(retry_count)
        seen_models.append(model_name)
        return model_name

    cfg.model_for_retry = spy

    for attempt in (1, 2, 3):
        model = FakeToolModel([AIMessage(content="tried")])
        run_runtime_wiring_agent(cfg, "boot failed", attempt=attempt, model_override=model)

    assert seen_models == [cfg.model_cheap, cfg.model_cheap, cfg.model_premium]
