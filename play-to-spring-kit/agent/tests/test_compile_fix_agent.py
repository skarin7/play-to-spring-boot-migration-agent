"""agents/compile_fix.py unit tests: model-tier routing for one LLM fix round."""

from langchain_core.messages import AIMessage

from agent.agents.compile_fix import run_compile_fix
from agent.config import AgentConfig


class FakeToolModel:
    def __init__(self, responses):
        self.responses = list(responses)

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        return self.responses.pop(0)


def test_item_count_from_cluster_count_escalates_tier(tmp_path):
    cfg = AgentConfig(spring_repo=tmp_path)
    assert cfg.escalate_item_threshold == 5
    clusters = [
        {"root_cause": "x", "representative": {"file": "A.java"}, "affected_files": [], "count": 1}
        for _ in range(5)
    ]
    seen_models = []
    orig_choose_model = cfg.choose_model

    def spy(signals):
        model_name = orig_choose_model(signals)
        seen_models.append(model_name)
        return model_name

    cfg.choose_model = spy
    model = FakeToolModel([AIMessage(content="tried")])

    run_compile_fix(cfg, clusters, retry_count=0, slice_id="slice-1", model_override=model)

    assert seen_models == [cfg.model_premium]
