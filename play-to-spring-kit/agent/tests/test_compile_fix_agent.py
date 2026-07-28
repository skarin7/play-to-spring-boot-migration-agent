"""_inline_affected_files (agent/agents/compile_fix.py): reads the files a
compile-error cluster points to and inlines their content into the fix
prompt, so the agent doesn't burn tool calls re-reading files it already
knows -- from cluster data -- it needs to fix."""

from error_clusterer import ErrorCluster
from langchain_core.messages import AIMessage

from agent.agents.compile_fix import (
    _MAX_INLINE_FILE_CHARS,
    _MAX_INLINE_FILES,
    _inline_affected_files,
    run_compile_fix,
)
from agent.config import AgentConfig


def _cluster(file: str, affected_files: list[str] | None = None) -> ErrorCluster:
    return ErrorCluster(
        root_cause="cannot find symbol",
        representative={"file": file, "line": 3, "message": "cannot find symbol"},
        affected_files=affected_files if affected_files is not None else [file],
        count=1,
    )


def test_inlines_representative_and_affected_file_content(tmp_path):
    a = tmp_path / "src/main/java/A.java"
    b = tmp_path / "src/main/java/B.java"
    a.parent.mkdir(parents=True)
    a.write_text("class A {}")
    b.write_text("class B {}")

    out = _inline_affected_files(tmp_path, [_cluster(str(a), [str(a), str(b)])])

    assert "class A {}" in out
    assert "class B {}" in out
    assert str(a) in out
    assert str(b) in out


def test_dedupes_same_file_across_clusters(tmp_path):
    a = tmp_path / "A.java"
    a.write_text("class A {}")

    out = _inline_affected_files(tmp_path, [_cluster(str(a)), _cluster(str(a))])

    assert out.count("class A {}") == 1


def test_skips_files_outside_spring_repo(tmp_path, monkeypatch):
    outside = tmp_path.parent / "outside.java"
    outside.write_text("class Outside {}")
    spring_repo = tmp_path / "spring"
    spring_repo.mkdir()

    out = _inline_affected_files(spring_repo, [_cluster(str(outside))])

    assert out == ""


def test_missing_file_skipped_not_raised(tmp_path):
    missing = tmp_path / "DoesNotExist.java"

    out = _inline_affected_files(tmp_path, [_cluster(str(missing))])

    assert out == ""


def test_relative_path_resolved_against_spring_repo(tmp_path):
    (tmp_path / "src").mkdir()
    f = tmp_path / "src" / "Rel.java"
    f.write_text("class Rel {}")

    out = _inline_affected_files(tmp_path, [_cluster("src/Rel.java")])

    assert "class Rel {}" in out


def test_large_file_truncated(tmp_path):
    big = tmp_path / "Big.java"
    big.write_text("x" * (_MAX_INLINE_FILE_CHARS + 500))

    out = _inline_affected_files(tmp_path, [_cluster(str(big))])

    assert "(truncated)" in out
    assert len(out) < _MAX_INLINE_FILE_CHARS + 500


def test_caps_at_max_inline_files(tmp_path):
    files = []
    for i in range(_MAX_INLINE_FILES + 3):
        f = tmp_path / f"F{i}.java"
        f.write_text(f"class F{i} {{}}")
        files.append(str(f))

    out = _inline_affected_files(tmp_path, [_cluster(files[0], files)])

    assert sum(out.count(f"class F{i} {{}}") for i in range(len(files))) == _MAX_INLINE_FILES


def test_no_clusters_returns_empty_string(tmp_path):
    assert _inline_affected_files(tmp_path, []) == ""


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
