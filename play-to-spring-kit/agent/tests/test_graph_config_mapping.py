"""Graph-level config-mapping-phase routing (M4): no-op cases, deterministic
seed-only resolution, agent-assisted leftover handling, attempts-exhausted,
budget-exhausted abort, and phase ordering (routes -> config_mapping -> verify).

Follows test_graph_routes.py's style: FakeCompiler/FakeFixer/FakeToolModel +
RuntimeCtx(...) + build_graph(cfg, ctx).compile() + graph.invoke(...).

NOTE on leftover semantics: diff_config_keys computes `leftover` purely from
`flatten_play_conf(conf_path)` (immutable within a run) and SEED_KEY_MAP
membership -- it does not (and structurally cannot, given its
`existing_keys: set[str]` signature) know whether an *unrelated* new Spring
key the agent appended actually "resolves" a specific non-seed leftover
entry (there is no naming convention linking e.g. "app.secret" to whatever
Spring-style key an LLM might invent for it). So for a genuinely unknown
(non-seed-table) key, `leftover` is invariant across config_mapping rounds
within a single run -- only the deterministic seed-table pass can ever empty
it out. The tests below reflect that: seed-table-only conf files resolve to
an empty leftover with zero agent involvement (test 2); a non-seed key
exercises the real agent tool-loop against the real fixture file and still
reaches success (test 3/4), but `leftover` stays populated in config-map.json
in that case, verified explicitly rather than assumed away.
"""

import json
from pathlib import Path

from langchain_core.messages import AIMessage

from agent.config import AgentConfig
from agent.graph import RuntimeCtx, build_graph, recursion_limit
from agent.tests.test_graph_flow import (
    FakeCompileResult,
    FakeCompiler,
    FakeFixer,
    FakeToolModel,
    _real_clusterer,
)

SEED_CONF_TXT = 'mongodb {\n  uri = "mongodb://localhost:27017/mydb"\n}\n'
LEFTOVER_CONF_TXT = 'app {\n  secret = "changeme"\n}\n'

ROUTES_TXT = "GET     /users                      controllers.UserController.list()\n"

UNMAPPED_JAVA = (
    "package controllers;\n\n"
    "import org.springframework.web.bind.annotation.*;\n\n"
    "@RestController\n"
    "public class UserController {\n"
    "    public ResponseEntity<List<User>> list() {\n"
    "        return null;\n"
    "    }\n"
    "}\n"
)


def _make_bootstrapped(spring_repo) -> None:
    (spring_repo / "pom.xml").write_text("<project/>")
    java_dir = spring_repo / "src" / "main" / "java" / "com" / "example"
    java_dir.mkdir(parents=True)
    (java_dir / "Application.java").write_text("class Application {}")
    props_dir = spring_repo / "src" / "main" / "resources"
    props_dir.mkdir(parents=True)
    (props_dir / "application.properties").write_text("")


def _write_controller(spring_repo, content: str) -> None:
    java_dir = spring_repo / "src" / "main" / "java" / "controllers"
    java_dir.mkdir(parents=True, exist_ok=True)
    (java_dir / "UserController.java").write_text(content, encoding="utf-8")


def make_config(tmp_path, conf_text: str | None = None, routes_text: str | None = None, **overrides) -> AgentConfig:
    play_repo = tmp_path / "play"
    play_repo.mkdir(exist_ok=True)
    if conf_text is not None or routes_text is not None:
        conf_dir = play_repo / "conf"
        conf_dir.mkdir(exist_ok=True)
        if conf_text is not None:
            (conf_dir / "application.conf").write_text(conf_text, encoding="utf-8")
        if routes_text is not None:
            (conf_dir / "routes").write_text(routes_text, encoding="utf-8")
    spring_repo = tmp_path / "spring"
    spring_repo.mkdir(exist_ok=True)
    _make_bootstrapped(spring_repo)
    cfg = AgentConfig(spring_repo=spring_repo, play_repo=play_repo, export_play_conf=True)
    cfg.api_key = "test-key"
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def run(cfg, ctx):
    graph = build_graph(cfg, ctx).compile()
    initial = {"spring_repo": str(cfg.spring_repo), "retry_count": 0, "total_llm_calls": 0}
    return graph.invoke(initial, config={"recursion_limit": recursion_limit(cfg)})


def properties_path(cfg) -> Path:
    return cfg.spring_repo / "src" / "main" / "resources" / "application.properties"


class RaisingModel:
    """Fails the test loudly if the config-mapping (or any) agent is ever invoked."""

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        raise AssertionError("LLM should not have been invoked in this scenario")


def _map_config_responses(new_content: str):
    """Two AIMessages: one tool call that rewrites application.properties, one that ends the loop."""
    return [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "write_file",
                    "args": {
                        "path": "src/main/resources/application.properties",
                        "content": new_content,
                    },
                    "id": "1",
                }
            ],
        ),
        AIMessage(content="done"),
    ]


def _map_route_responses():
    return [
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


# ----------------------------------------------------------------------
# 1. No-op cases: no play_repo, export disabled, no conf/application.conf,
#    or no application.properties yet -> no-op, run still succeeds.
# ----------------------------------------------------------------------


def test_no_play_repo_config_mapping_is_noop(tmp_path):
    cfg = AgentConfig(spring_repo=tmp_path)
    cfg.api_key = "test-key"
    compiler = FakeCompiler([FakeCompileResult(0)])
    ctx = RuntimeCtx(compiler, FakeFixer(), _real_clusterer(), model_override=RaisingModel())
    final = run(cfg, ctx)
    assert final["run_outcome"] == "success"
    assert final.get("config_mapping_attempts", 0) == 0
    config_map = json.loads((cfg.migration_dir / "config-map.json").read_text())
    assert config_map == {"seed_mapped": {}, "leftover": {}}


def test_export_play_conf_disabled_is_noop(tmp_path):
    cfg = make_config(tmp_path, conf_text=SEED_CONF_TXT, export_play_conf=False)
    compiler = FakeCompiler([FakeCompileResult(0)])
    ctx = RuntimeCtx(compiler, FakeFixer(), _real_clusterer(), model_override=RaisingModel())
    final = run(cfg, ctx)
    assert final["run_outcome"] == "success"
    assert final.get("config_mapping_attempts", 0) == 0
    # Deterministic seed pass never ran: the property was not appended.
    assert "spring.data.mongodb.uri" not in properties_path(cfg).read_text(encoding="utf-8")


def test_no_conf_application_conf_is_noop(tmp_path):
    cfg = make_config(tmp_path, conf_text=None)
    compiler = FakeCompiler([FakeCompileResult(0)])
    ctx = RuntimeCtx(compiler, FakeFixer(), _real_clusterer(), model_override=RaisingModel())
    final = run(cfg, ctx)
    assert final["run_outcome"] == "success"
    assert final.get("config_mapping_attempts", 0) == 0


def test_no_application_properties_yet_is_noop(tmp_path):
    """application.properties is present at bootstrap time (satisfying the
    bootstrap gate) but disappears before config_mapping_node runs (e.g. a
    transform step wiped it) -- config_mapping must still no-op cleanly."""
    cfg = make_config(tmp_path, conf_text=SEED_CONF_TXT)

    def _deleting_jar_runner(config, path_prefix):
        props = properties_path(config)
        if props.exists():
            props.unlink()
        return 0, 0

    compiler = FakeCompiler([FakeCompileResult(0), FakeCompileResult(0)])
    ctx = RuntimeCtx(
        compiler, FakeFixer(), _real_clusterer(), model_override=RaisingModel(), jar_runner=_deleting_jar_runner
    )
    final = run(cfg, ctx)
    assert final["run_outcome"] == "success"
    assert final.get("config_mapping_attempts", 0) == 0
    config_map = json.loads((cfg.migration_dir / "config-map.json").read_text())
    assert config_map == {"seed_mapped": {}, "leftover": {}}


# ----------------------------------------------------------------------
# 2. Only seed-table keys present: deterministic pass alone resolves
#    everything, agent never invoked.
# ----------------------------------------------------------------------


def test_seed_only_conf_resolves_without_agent(tmp_path):
    cfg = make_config(tmp_path, conf_text=SEED_CONF_TXT)
    compiler = FakeCompiler([FakeCompileResult(0), FakeCompileResult(0)])
    ctx = RuntimeCtx(compiler, FakeFixer(), _real_clusterer(), model_override=RaisingModel())
    final = run(cfg, ctx)
    assert final["run_outcome"] == "success"
    assert final.get("total_llm_calls", 0) == 0
    assert final.get("config_mapping_attempts", 0) == 0
    config_map = json.loads((cfg.migration_dir / "config-map.json").read_text())
    assert config_map["leftover"] == {}
    assert config_map["seed_mapped"] == {"spring.data.mongodb.uri": "mongodb://localhost:27017/mydb"}
    text = properties_path(cfg).read_text(encoding="utf-8")
    assert "spring.data.mongodb.uri=mongodb://localhost:27017/mydb" in text


# ----------------------------------------------------------------------
# 3. Non-seed key present: agent invoked, its edit really lands in the
#    fixture file (honest integration, not mocked).
# ----------------------------------------------------------------------


def test_unknown_key_invokes_agent_and_edit_lands_in_real_file(tmp_path):
    cfg = make_config(tmp_path, conf_text=LEFTOVER_CONF_TXT, max_config_mapping_attempts=1)
    compiler = FakeCompiler([FakeCompileResult(0), FakeCompileResult(0)])
    new_content = "app.secret=changeme\napp.security.secret=changeme\n"
    model = FakeToolModel(_map_config_responses(new_content))
    ctx = RuntimeCtx(compiler, FakeFixer(), _real_clusterer(), model_override=model)
    final = run(cfg, ctx)
    assert final["run_outcome"] == "success"
    assert final["total_llm_calls"] == 1
    assert final["config_mapping_attempts"] == 1
    text = properties_path(cfg).read_text(encoding="utf-8")
    assert "app.security.secret=changeme" in text
    config_map = json.loads((cfg.migration_dir / "config-map.json").read_text())
    # `leftover` is computed purely from the immutable conf file + SEED_KEY_MAP
    # membership (see module docstring) -- it stays populated even though the
    # agent made a real, useful edit; the phase is still non-blocking.
    assert config_map["leftover"] == {"app.secret": "changeme"}


# ----------------------------------------------------------------------
# 4. Non-seed key remains after max_config_mapping_attempts: non-blocking.
# ----------------------------------------------------------------------


def test_unknown_key_remains_after_max_attempts_is_non_blocking(tmp_path):
    cfg = make_config(tmp_path, conf_text=LEFTOVER_CONF_TXT, max_config_mapping_attempts=1)
    compiler = FakeCompiler([FakeCompileResult(0), FakeCompileResult(0)])
    # Agent "tries" but never actually adds anything useful.
    model = FakeToolModel([AIMessage(content="tried but gave up")])
    ctx = RuntimeCtx(compiler, FakeFixer(), _real_clusterer(), model_override=model)
    final = run(cfg, ctx)
    assert final["run_outcome"] == "success"
    assert final["config_mapping_attempts"] == 1
    config_map = json.loads((cfg.migration_dir / "config-map.json").read_text())
    assert config_map["leftover"] == {"app.secret": "changeme"}


# ----------------------------------------------------------------------
# 4b. Regression: a seed-mapped key applied on an earlier loop round must
#     not be lost from config-map.json's audit record just because a later
#     round's own diff for it comes up empty (already on disk) while a
#     separate leftover key keeps forcing more loop rounds.
# ----------------------------------------------------------------------


def test_seed_mapped_persists_across_loop_rounds_not_lost(tmp_path):
    conf_text = SEED_CONF_TXT + LEFTOVER_CONF_TXT
    cfg = make_config(tmp_path, conf_text=conf_text, max_config_mapping_attempts=2)
    compiler = FakeCompiler([FakeCompileResult(0), FakeCompileResult(0)])
    # Agent "tries" each round but never actually resolves app.secret --
    # forces exactly 2 loop rounds (max_config_mapping_attempts=2) before the
    # phase gives up non-blockingly.
    model = FakeToolModel([AIMessage(content="tried but gave up")] * 2)
    ctx = RuntimeCtx(compiler, FakeFixer(), _real_clusterer(), model_override=model)
    final = run(cfg, ctx)

    assert final["run_outcome"] == "success"
    assert final["total_llm_calls"] == 2
    assert final["config_mapping_attempts"] == 2

    config_map = json.loads((cfg.migration_dir / "config-map.json").read_text())
    # mongodb.uri was seed-mapped and applied on round 1 (before the leftover
    # key even reached the agent) -- it must still show up in the FINAL
    # config-map.json, not just in application.properties on disk.
    assert config_map["seed_mapped"] == {"spring.data.mongodb.uri": "mongodb://localhost:27017/mydb"}
    assert config_map["leftover"] == {"app.secret": "changeme"}
    text = properties_path(cfg).read_text(encoding="utf-8")
    assert "spring.data.mongodb.uri=mongodb://localhost:27017/mydb" in text


# ----------------------------------------------------------------------
# 5. Global budget exhausted during config-mapping rounds aborts the run.
# ----------------------------------------------------------------------


def test_config_mapping_respects_global_budget_zero_never_invokes_agent(tmp_path):
    cfg = make_config(tmp_path, conf_text=LEFTOVER_CONF_TXT, max_total_llm_calls=0)
    compiler = FakeCompiler([FakeCompileResult(0)])
    ctx = RuntimeCtx(compiler, FakeFixer(), _real_clusterer(), model_override=RaisingModel())
    final = run(cfg, ctx)
    assert final["run_outcome"] == "budget_exhausted"
    assert final["run_exit_code"] == 4
    assert final["total_llm_calls"] == 0
    config_map = json.loads((cfg.migration_dir / "config-map.json").read_text())
    assert config_map["leftover"] == {"app.secret": "changeme"}


# ----------------------------------------------------------------------
# 6. Full phase ordering: routes resolves first, config_mapping second,
#    verify/run_done last.
# ----------------------------------------------------------------------


def test_routes_then_config_mapping_then_verify_ordering(tmp_path):
    cfg = make_config(
        tmp_path, conf_text=LEFTOVER_CONF_TXT, routes_text=ROUTES_TXT, max_config_mapping_attempts=1
    )
    _write_controller(cfg.spring_repo, UNMAPPED_JAVA)
    compiler = FakeCompiler([FakeCompileResult(0), FakeCompileResult(0), FakeCompileResult(0)])
    new_content = "app.secret=changeme\napp.security.secret=changeme\n"
    # Routes-agent responses consumed first, then config-mapping-agent
    # responses -- if the graph invoked config_mapping before routes, the
    # routes agent would instead receive the config-mapping write_file
    # response (wrong tool name for its str_replace call) and the java file
    # would never get its @GetMapping annotation.
    model = FakeToolModel(_map_route_responses() + _map_config_responses(new_content))
    ctx = RuntimeCtx(compiler, FakeFixer([0]), _real_clusterer(), model_override=model)
    final = run(cfg, ctx)

    assert final["run_outcome"] == "success"
    assert final["total_llm_calls"] == 2

    route_map = final.get("route_map")
    assert route_map is not None
    assert route_map["unmapped"] == []
    java_text = (cfg.spring_repo / "src/main/java/controllers/UserController.java").read_text()
    assert '@GetMapping("/users")' in java_text

    config_map = final.get("config_map")
    assert config_map is not None
    assert "app.security.secret=changeme" in properties_path(cfg).read_text(encoding="utf-8")
