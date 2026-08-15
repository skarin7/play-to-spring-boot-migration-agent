"""Graph-level T2 signature-check wiring (M6): the node runs after
slice_finalize (slice-scoped) and after verify (whole-tree, before boot_run),
never gates the run by itself, and is a silent no-op when there's no
play_repo/jar/signature_runner configured -- matching every other optional
pre-flight signal in this codebase (inventory_runner, jar_runner).

Follows test_graph_boot.py's style: FakeCompiler/FakeFixer/FakeBootRunner +
RuntimeCtx(...) + build_graph(cfg, ctx).compile() + graph.invoke(...).
"""

from agent.config import AgentConfig
from agent.graph import RuntimeCtx, build_graph, recursion_limit
from agent.state import EXIT_OK
from agent.tests.test_graph_flow import (
    FakeCompileResult,
    FakeCompiler,
    FakeFixer,
    _real_clusterer,
)
from agent.tools.maven import BootResult


class FakeBootRunner:
    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    def __call__(self, config):
        self.calls += 1
        return self.results.pop(0)


class RaisingModel:
    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        raise AssertionError("LLM should not have been invoked in this scenario")


def _make_play_and_spring_trees(tmp_path):
    play_repo = tmp_path / "play"
    (play_repo / "app" / "controllers").mkdir(parents=True)
    (play_repo / "app" / "controllers" / "UserController.java").write_text(
        "package controllers;\npublic class UserController {\n"
        "    public String list() { return \"ok\"; }\n"
        "    public String detail(int id) { return \"ok\"; }\n"
        "}\n"
    )

    spring_repo = tmp_path / "spring"
    (spring_repo / "src" / "main" / "java" / "controllers").mkdir(parents=True)
    (spring_repo / "pom.xml").write_text("<project/>")
    java_dir = spring_repo / "src" / "main" / "java" / "com" / "example"
    java_dir.mkdir(parents=True)
    (java_dir / "Application.java").write_text("class Application {}")
    props_dir = spring_repo / "src" / "main" / "resources"
    props_dir.mkdir(parents=True)
    (props_dir / "application.properties").write_text("")
    (spring_repo / ".migration").mkdir(parents=True, exist_ok=True)
    (spring_repo / ".migration" / "decisions.md").write_text("# Migration Decisions\n")

    jar_path = play_repo / "dev-toolkit-1.0.0.jar"
    jar_path.write_bytes(b"fake jar")  # only .is_file() is checked by the node

    return play_repo, spring_repo, jar_path


def make_config(tmp_path, **overrides) -> AgentConfig:
    play_repo, spring_repo, jar_path = _make_play_and_spring_trees(tmp_path)
    cfg = AgentConfig(spring_repo=spring_repo, play_repo=play_repo, jar_path=jar_path)
    cfg.api_key = "test-key"
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def run(cfg, ctx):
    graph = build_graph(cfg, ctx).compile()
    initial = {"spring_repo": str(cfg.spring_repo), "retry_count": 0, "total_llm_calls": 0}
    return graph.invoke(initial, config={"recursion_limit": recursion_limit(cfg)})


def _make_signature_runner(play_report, spring_report):
    calls = []

    def runner(config, play_root, spring_root):
        calls.append((play_root, spring_root))
        return play_report, spring_report

    return runner, calls


def _default_ctx_kwargs(cfg):
    return dict(
        compiler=FakeCompiler([FakeCompileResult(0)]),
        fixer=FakeFixer(),
        clusterer=_real_clusterer(),
        model_override=RaisingModel(),
        jar_runner=lambda cfg_, prefix: (1, 0),  # 1 file added, matching migration_output_plausible
        boot_runner=FakeBootRunner([BootResult(started=True, log_tail="Started App in 1.0 seconds")]),
    )


# ----------------------------------------------------------------------
# 1. No signature_runner configured -> silent no-op, run still succeeds
#    (backward-compat: every existing RuntimeCtx() in older tests has no
#    signature_runner set).
# ----------------------------------------------------------------------


def test_no_signature_runner_configured_run_still_succeeds(tmp_path):
    cfg = make_config(tmp_path)
    ctx = RuntimeCtx(**_default_ctx_kwargs(cfg))
    final = run(cfg, ctx)
    assert final["run_outcome"] == "success"
    assert final["run_exit_code"] == EXIT_OK
    assert final.get("signature_findings", []) == []


# ----------------------------------------------------------------------
# 2. method_missing surfaces as a finding but does not block the run --
#    matches routes/config_mapping's non-blocking-soft-finding model.
# ----------------------------------------------------------------------


def test_method_missing_recorded_as_finding_does_not_block_run(tmp_path):
    cfg = make_config(tmp_path)
    play_report = {
        "files": {
            "controllers/UserController.java": {
                "path": "controllers/UserController.java",
                "class": "UserController",
                "methods": [
                    {"name": "list", "arity": 0, "visibility": "public", "returns": "reference", "statements": 1},
                    {"name": "detail", "arity": 1, "visibility": "public", "returns": "reference", "statements": 1},
                ],
                "fields": [],
            }
        }
    }
    spring_report = {
        "files": {
            "controllers/UserController.java": {
                "path": "controllers/UserController.java",
                "class": "UserController",
                "methods": [
                    {"name": "list", "arity": 0, "visibility": "public", "returns": "reference", "statements": 1},
                ],
                "fields": [],
            }
        }
    }
    runner, calls = _make_signature_runner(play_report, spring_report)
    ctx = RuntimeCtx(signature_runner=runner, **_default_ctx_kwargs(cfg))

    final = run(cfg, ctx)

    assert final["run_outcome"] == "success"
    assert final["run_exit_code"] == EXIT_OK
    findings = final.get("signature_findings", [])
    method_missing = [f for f in findings if f["category"] == "method_missing"]
    assert len(method_missing) >= 1
    assert method_missing[0]["method"] == "detail"
    assert method_missing[0]["severity"] == "blocker"
    assert method_missing[0]["tier"] == "T2"
    # signature_runner invoked at least twice: once slice-scoped after
    # slice_finalize, once unscoped (final) between verify and boot_run.
    assert len(calls) >= 2


# ----------------------------------------------------------------------
# 3. Clean signatures on both sides -> no findings.
# ----------------------------------------------------------------------


def test_matching_signatures_no_findings(tmp_path):
    cfg = make_config(tmp_path)
    same_report = {
        "files": {
            "controllers/UserController.java": {
                "path": "controllers/UserController.java",
                "class": "UserController",
                "methods": [
                    {"name": "list", "arity": 0, "visibility": "public", "returns": "reference", "statements": 1},
                    {"name": "detail", "arity": 1, "visibility": "public", "returns": "reference", "statements": 1},
                ],
                "fields": [],
            }
        }
    }
    runner, _ = _make_signature_runner(same_report, same_report)
    ctx = RuntimeCtx(signature_runner=runner, **_default_ctx_kwargs(cfg))

    final = run(cfg, ctx)

    assert final["run_outcome"] == "success"
    assert final.get("signature_findings", []) == []


# ----------------------------------------------------------------------
# 4. Missing app/ dir on the Play side (repo shape signature can't scan) ->
#    treated as no signal, not a crash.
# ----------------------------------------------------------------------


def test_missing_play_java_root_is_noop_not_crash(tmp_path):
    cfg = make_config(tmp_path)
    import shutil

    shutil.rmtree(cfg.play_repo / "app")
    runner, calls = _make_signature_runner({"files": {}}, {"files": {}})
    ctx = RuntimeCtx(signature_runner=runner, **_default_ctx_kwargs(cfg))

    final = run(cfg, ctx)

    assert final["run_outcome"] == "success"
    assert final.get("signature_findings", []) == []
    assert calls == []
