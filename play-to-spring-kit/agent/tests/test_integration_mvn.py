"""End-to-end tests against a real Maven project. No LLM — deterministic path only.

Skipped when mvn is not installed.
"""

import shutil
from pathlib import Path

import pytest

from agent.config import AgentConfig
from agent.graph import build_graph, default_ctx, recursion_limit

pytestmark = pytest.mark.skipif(shutil.which("mvn") is None, reason="mvn not installed")

FIXTURE = Path(__file__).parent / "fixtures" / "mini-spring"


@pytest.fixture
def mini_spring(tmp_path) -> Path:
    repo = tmp_path / "mini-spring"
    shutil.copytree(FIXTURE, repo)
    return repo


def install_broken(repo: Path, name: str) -> None:
    src = repo / "broken" / name
    dst = repo / "src" / "main" / "java" / "com" / "example" / name
    shutil.copy(src, dst)
    shutil.rmtree(repo / "broken")


def run_graph(repo: Path, api_key: str | None = None):
    cfg = AgentConfig(spring_repo=repo)
    cfg.api_key = api_key
    graph = build_graph(cfg, default_ctx(cfg)).compile()
    initial = {"spring_repo": str(repo), "slice_id": "it", "retry_count": 0, "total_llm_calls": 0}
    return graph.invoke(initial, config={"recursion_limit": recursion_limit(cfg)})


def test_clean_project_compiles_green(mini_spring):
    shutil.rmtree(mini_spring / "broken")
    final = run_graph(mini_spring)
    assert final["outcome"] == "success"
    assert final["exit_code"] == 0


def test_missing_guava_dep_fixed_by_pom_fix(mini_spring):
    install_broken(mini_spring, "MissingDep.java")
    final = run_graph(mini_spring)
    assert final["outcome"] == "success", final.get("last_compile", {}).get("log_tail")
    pom = (mini_spring / "pom.xml").read_text()
    assert "<artifactId>guava</artifactId>" in pom
    assert final["total_llm_calls"] == 0


def test_unfixable_error_without_llm_exits_2(mini_spring):
    install_broken(mini_spring, "Unfixable.java")
    final = run_graph(mini_spring, api_key=None)
    assert final["outcome"] == "no_llm"
    assert final["exit_code"] == 2
    assert final["last_compile"]["error_count"] >= 1
