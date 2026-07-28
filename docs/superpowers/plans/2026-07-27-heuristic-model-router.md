# Heuristic Model Router Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `AgentConfig.model_for_retry(retry_count)` — which escalates
cheap→premium on retry count alone — with `AgentConfig.choose_model(TaskSignals)`,
which also escalates on a phase-supplied `item_count` (errors/unmapped
routes/leftover keys/boot failures), wired into `compile_fix`, `config_mapping`,
`routes`, and `runtime_wiring`. `bootstrap` is explicitly excluded and keeps its
hardcoded `model_premium` (see spec's Per-phase signal wiring section for why).

**Architecture:** One pure function in `config.py` (`TaskSignals` dataclass +
`AgentConfig.choose_model`), one new env-configurable threshold
(`escalate_item_threshold`), and a one-line swap at each of the 4 agent call
sites (`agents/compile_fix.py`, `agents/config_mapping.py`, `agents/routes.py`,
`agents/runtime_wiring.py`) plus their module docstrings, which currently
document routes/config_mapping as intentionally cheap-only.

**Tech Stack:** Python 3.12, pytest, dataclasses (no new dependencies).

**Spec:** `docs/superpowers/specs/2026-07-27-heuristic-model-router-design.md`

---

### Task 1: `TaskSignals` + `choose_model` in `config.py`

**Files:**
- Modify: `play-to-spring-kit/agent/config.py:82-117`
- Test: `play-to-spring-kit/agent/tests/test_model_router.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `play-to-spring-kit/agent/tests/test_model_router.py`:

```python
"""config.py's choose_model/TaskSignals: heuristic cheap/premium routing
based on retry_count OR item_count, replacing the old retry-count-only
model_for_retry."""

from agent.config import AgentConfig, TaskSignals


def test_neither_threshold_crossed_stays_cheap(tmp_path):
    cfg = AgentConfig(spring_repo=tmp_path)
    assert cfg.choose_model(TaskSignals(retry_count=0, item_count=0)) == cfg.model_cheap


def test_retry_count_at_threshold_escalates(tmp_path):
    cfg = AgentConfig(spring_repo=tmp_path)
    assert cfg.escalate_after_retries == 2
    assert cfg.choose_model(TaskSignals(retry_count=2, item_count=0)) == cfg.model_premium
    assert cfg.choose_model(TaskSignals(retry_count=1, item_count=0)) == cfg.model_cheap


def test_item_count_at_threshold_escalates(tmp_path):
    cfg = AgentConfig(spring_repo=tmp_path)
    assert cfg.escalate_item_threshold == 5
    assert cfg.choose_model(TaskSignals(retry_count=0, item_count=5)) == cfg.model_premium
    assert cfg.choose_model(TaskSignals(retry_count=0, item_count=4)) == cfg.model_cheap


def test_either_signal_crossing_is_enough(tmp_path):
    cfg = AgentConfig(spring_repo=tmp_path)
    assert cfg.choose_model(TaskSignals(retry_count=2, item_count=0)) == cfg.model_premium
    assert cfg.choose_model(TaskSignals(retry_count=0, item_count=5)) == cfg.model_premium


def test_escalate_item_threshold_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv("MIGRATION_ESCALATE_ITEM_THRESHOLD", "3")
    cfg = AgentConfig(spring_repo=tmp_path)
    assert cfg.escalate_item_threshold == 3
    assert cfg.choose_model(TaskSignals(retry_count=0, item_count=3)) == cfg.model_premium
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd play-to-spring-kit && .venv/bin/python -m pytest agent/tests/test_model_router.py -v`
Expected: FAIL — `ImportError: cannot import name 'TaskSignals' from 'agent.config'`

- [ ] **Step 3: Implement `TaskSignals` and `choose_model`, remove `model_for_retry`**

In `play-to-spring-kit/agent/config.py`, add `escalate_item_threshold` next to
`escalate_after_retries` (around line 88-90):

```python
    escalate_after_retries: int = field(
        default_factory=lambda: _env_int("ESCALATE_AFTER_RETRIES", 2)
    )
    escalate_item_threshold: int = field(
        default_factory=lambda: _env_int("MIGRATION_ESCALATE_ITEM_THRESHOLD", 5)
    )
```

Replace `model_for_retry` (lines 113-117) with:

```python
    def choose_model(self, signals: "TaskSignals") -> str:
        """Two-tier heuristic routing: escalate to model_premium when either
        the retry count or the task's item_count (phase-specific magnitude of
        remaining work -- error clusters, unmapped routes, leftover config
        keys, boot-failure count) crosses its threshold."""
        if signals.retry_count >= self.escalate_after_retries:
            return self.model_premium
        if signals.item_count >= self.escalate_item_threshold:
            return self.model_premium
        return self.model_cheap
```

Add the `TaskSignals` dataclass after the `AgentConfig` class (end of file,
after line 117/118):

```python


@dataclass
class TaskSignals:
    """Complexity signals fed to AgentConfig.choose_model. item_count's
    meaning is phase-specific (error clusters, unmapped routes, leftover
    config keys, boot-failure count) -- see docs/superpowers/specs/
    2026-07-27-heuristic-model-router-design.md."""

    retry_count: int = 0
    item_count: int = 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd play-to-spring-kit && .venv/bin/python -m pytest agent/tests/test_model_router.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add play-to-spring-kit/agent/config.py play-to-spring-kit/agent/tests/test_model_router.py
git commit -m "feat: add TaskSignals/choose_model heuristic model router to AgentConfig"
```

---

### Task 2: Wire `compile_fix` agent to `choose_model`

**Files:**
- Modify: `play-to-spring-kit/agent/agents/compile_fix.py:108`
- Test: `play-to-spring-kit/agent/tests/test_compile_fix_agent.py`

- [ ] **Step 1: Write the failing test**

Add to `play-to-spring-kit/agent/tests/test_compile_fix_agent.py` (needs a
`FakeToolModel` and `run_compile_fix`/`AgentConfig` import — add these imports
at the top alongside the existing ones):

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd play-to-spring-kit && .venv/bin/python -m pytest agent/tests/test_compile_fix_agent.py::test_item_count_from_cluster_count_escalates_tier -v`
Expected: FAIL — `assert [cfg.model_cheap] == [cfg.model_premium]` (still calling old `model_for_retry(retry_count)` with `retry_count=0`, so no escalation happens yet)

- [ ] **Step 3: Wire `choose_model` at the call site**

In `play-to-spring-kit/agent/agents/compile_fix.py`, add the import (line 15-17):

```python
from ..config import AgentConfig, TaskSignals
```

Replace line 108:

```python
    model_name = config.model_for_retry(retry_count)
```

with:

```python
    model_name = config.choose_model(TaskSignals(retry_count=retry_count, item_count=len(clusters)))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd play-to-spring-kit && .venv/bin/python -m pytest agent/tests/test_compile_fix_agent.py -v`
Expected: all passed, including the new test

- [ ] **Step 5: Commit**

```bash
git add play-to-spring-kit/agent/agents/compile_fix.py play-to-spring-kit/agent/tests/test_compile_fix_agent.py
git commit -m "feat: route compile_fix model choice through choose_model (adds cluster-count signal)"
```

---

### Task 3: Wire `runtime_wiring` agent to `choose_model`

**Files:**
- Modify: `play-to-spring-kit/agent/agents/runtime_wiring.py:1-11,90`
- Modify: `play-to-spring-kit/agent/tests/test_runtime_wiring_agent.py`

- [ ] **Step 1: Update the existing tier-escalation test to fail against the new API**

In `play-to-spring-kit/agent/tests/test_runtime_wiring_agent.py`, replace the
`test_tier_escalates_cheap_then_premium_after_escalate_after_retries` test
(lines 74-93) and the top import:

```python
from agent.agents.runtime_wiring import load_runtime_wiring_reference, run_runtime_wiring_agent
from agent.config import AgentConfig, TaskSignals
```

```python
def test_tier_escalates_cheap_then_premium_after_escalate_after_retries(tmp_path):
    """attempt N maps to retry_count N-1 (agent_node's 0-indexed convention):
    with the default escalate_after_retries=2, attempts 1-2 are cheap,
    attempt 3+ is premium."""
    cfg = AgentConfig(spring_repo=tmp_path)
    seen_models: list[str] = []
    orig_choose_model = cfg.choose_model

    def spy(signals):
        model_name = orig_choose_model(signals)
        seen_models.append(model_name)
        return model_name

    cfg.choose_model = spy

    for attempt in (1, 2, 3):
        model = FakeToolModel([AIMessage(content="tried")])
        run_runtime_wiring_agent(cfg, "boot failed", attempt=attempt, model_override=model)

    assert seen_models == [cfg.model_cheap, cfg.model_cheap, cfg.model_premium]


def test_item_count_from_caused_by_count_escalates_tier(tmp_path):
    cfg = AgentConfig(spring_repo=tmp_path)
    assert cfg.escalate_item_threshold == 5
    boot_log = "\n".join([f"Caused by: java.lang.Exception {i}" for i in range(5)])
    seen_signals = []
    orig_choose_model = cfg.choose_model

    def spy(signals):
        seen_signals.append(signals)
        return orig_choose_model(signals)

    cfg.choose_model = spy
    model = FakeToolModel([AIMessage(content="tried")])

    run_runtime_wiring_agent(cfg, boot_log, attempt=1, model_override=model)

    assert seen_signals == [TaskSignals(retry_count=0, item_count=5)]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd play-to-spring-kit && .venv/bin/python -m pytest agent/tests/test_runtime_wiring_agent.py -v`
Expected: FAIL — `AttributeError`/`AssertionError` (old code still calls `config.model_for_retry`, which no longer exists after Task 1, so this will error rather than just assert-fail; that's fine, it's still a failing test confirming the call site isn't updated yet)

- [ ] **Step 3: Wire `choose_model` and item_count at the call site**

In `play-to-spring-kit/agent/agents/runtime_wiring.py`, update the module
docstring (lines 1-11) to drop the now-inaccurate "one M4 phase that
escalates" claim:

```python
"""Runtime-wiring agent: fixes Spring Boot startup failures (bean wiring, config).

``verify_node``/the compile-fix subgraph only prove the code *compiles* --
missing beans, bad `@ConditionalOnExpression` gaps, and other runtime-only
wiring failures (see docs/compile-fixes-for-toolkit.md's "Spring Boot
runtime" table) are invisible until `mvn spring-boot:run` is actually
attempted (agent/tools/maven.py). Model tier is picked by
config.choose_model: escalates to premium on repeated retries or a boot log
with several distinct "Caused by:" failures (see docs/superpowers/specs/
2026-07-27-heuristic-model-router-design.md).
"""
```

Add the import (line 20):

```python
from ..config import AgentConfig, TaskSignals
```

Add a helper above `run_runtime_wiring_agent` (after `_user_prompt`, before
line 83):

```python
def _caused_by_count(boot_log_tail: str) -> int:
    return max(1, boot_log_tail.count("Caused by:"))
```

Replace line 90:

```python
    model_name = config.model_for_retry(attempt - 1)
```

with:

```python
    model_name = config.choose_model(
        TaskSignals(retry_count=attempt - 1, item_count=_caused_by_count(boot_log_tail))
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd play-to-spring-kit && .venv/bin/python -m pytest agent/tests/test_runtime_wiring_agent.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add play-to-spring-kit/agent/agents/runtime_wiring.py play-to-spring-kit/agent/tests/test_runtime_wiring_agent.py
git commit -m "feat: route runtime_wiring model choice through choose_model (adds Caused-by-count signal)"
```

---

### Task 4: Wire `config_mapping` agent to `choose_model`

**Files:**
- Modify: `play-to-spring-kit/agent/agents/config_mapping.py:1-11,63-70`
- Test: `play-to-spring-kit/agent/tests/test_config_mapping_agent.py`

- [ ] **Step 1: Write the failing test**

Add to `play-to-spring-kit/agent/tests/test_config_mapping_agent.py` (add
`TaskSignals` to the existing `from agent.config import AgentConfig` import,
making it `from agent.config import AgentConfig, TaskSignals`):

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd play-to-spring-kit && .venv/bin/python -m pytest agent/tests/test_config_mapping_agent.py::test_retry_count_and_leftover_count_signals_reach_choose_model -v`
Expected: FAIL — `AttributeError: 'AgentConfig' object attribute 'choose_model' is read-only`-equivalent won't occur; instead it fails because `run_config_mapping_agent` never calls `cfg.choose_model` at all yet, so `seen_signals == []`

- [ ] **Step 3: Wire `choose_model` at the call site**

In `play-to-spring-kit/agent/agents/config_mapping.py`, update the module
docstring (lines 1-11) — drop the "cheap tier only, no escalation" claim:

```python
"""Config-mapping agent: adds Spring-idiomatic keys for leftover Play config keys.

``scripts/conf_to_application_properties.py`` already flattens Play's
``conf/application.conf`` into ``<spring_repo>/src/main/resources/application.properties``
verbatim (dot-keys, e.g. ``mongodb.uri=...``) but never renames them to
Spring's own idiomatic property names (e.g. ``spring.data.mongodb.uri``).
``tools/config_mapping.py``'s seed table already resolves the well-known
cases deterministically; this agent handles everything else. Model tier is
picked by config.choose_model: escalates to premium on repeated retries or a
large leftover-key count (see docs/superpowers/specs/
2026-07-27-heuristic-model-router-design.md).
"""
```

Update the import (line 20):

```python
from ..config import AgentConfig, TaskSignals
```

Replace line 70:

```python
    model_name = config.model_cheap
```

with:

```python
    model_name = config.choose_model(TaskSignals(retry_count=attempt - 1, item_count=len(leftover)))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd play-to-spring-kit && .venv/bin/python -m pytest agent/tests/test_config_mapping_agent.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add play-to-spring-kit/agent/agents/config_mapping.py play-to-spring-kit/agent/tests/test_config_mapping_agent.py
git commit -m "feat: route config_mapping model choice through choose_model"
```

---

### Task 5: Wire `routes` agent to `choose_model`

**Files:**
- Modify: `play-to-spring-kit/agent/agents/routes.py:1-8,58-65`
- Test: `play-to-spring-kit/agent/tests/test_routes_agent.py`

- [ ] **Step 1: Write the failing test**

Add to `play-to-spring-kit/agent/tests/test_routes_agent.py` (add
`TaskSignals` to the existing `from agent.config import AgentConfig` import,
making it `from agent.config import AgentConfig, TaskSignals`):

```python
def test_retry_count_and_unmapped_count_signals_reach_choose_model(tmp_path):
    cfg = AgentConfig(spring_repo=tmp_path)
    java_dir = tmp_path / "src" / "main" / "java" / "controllers"
    java_dir.mkdir(parents=True)
    (java_dir / "UserController.java").write_text(
        "package controllers;\n@RestController\npublic class UserController {}\n", encoding="utf-8"
    )

    seen_signals = []
    orig_choose_model = cfg.choose_model

    def spy(signals):
        seen_signals.append(signals)
        return orig_choose_model(signals)

    cfg.choose_model = spy
    model = FakeToolModel([AIMessage(content="done")])

    run_routes_agent(cfg, UNMAPPED, attempt=1, model_override=model)

    assert seen_signals == [TaskSignals(retry_count=0, item_count=len(UNMAPPED))]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd play-to-spring-kit && .venv/bin/python -m pytest agent/tests/test_routes_agent.py::test_retry_count_and_unmapped_count_signals_reach_choose_model -v`
Expected: FAIL — `seen_signals == []` (call site never calls `choose_model` yet)

- [ ] **Step 3: Wire `choose_model` at the call site**

In `play-to-spring-kit/agent/agents/routes.py`, update the module docstring
(lines 1-8) — drop the "cheap tier only, no escalation" claim:

```python
"""Routes agent: annotates Spring controller methods with ``@*Mapping`` routes.

Play ``conf/routes`` maps HTTP routes to controller methods; the toolkit JAR
migrates the method bodies but never adds the Spring routing annotations
(docs/play_to_spring_migration.md 6.4/7.1: "routes | @RestController +
@*Mapping"). Model tier is picked by config.choose_model: escalates to
premium on repeated retries or a large unmapped-route count (see
docs/superpowers/specs/2026-07-27-heuristic-model-router-design.md).
"""
```

Update the import (line 17):

```python
from ..config import AgentConfig, TaskSignals
```

Replace line 65:

```python
    model_name = config.model_cheap
```

with:

```python
    model_name = config.choose_model(TaskSignals(retry_count=attempt - 1, item_count=len(unmapped)))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd play-to-spring-kit && .venv/bin/python -m pytest agent/tests/test_routes_agent.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add play-to-spring-kit/agent/agents/routes.py play-to-spring-kit/agent/tests/test_routes_agent.py
git commit -m "feat: route routes-agent model choice through choose_model"
```

---

### Task 6: Full suite regression check

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `cd play-to-spring-kit && .venv/bin/python -m pytest agent/tests -q`
Expected: all tests pass, no failures/errors. In particular confirm
`test_bootstrap_agent.py` and `test_graph_bootstrap.py` still pass unmodified
(bootstrap keeps hardcoded `model_premium`), and grep confirms no leftover
references to the removed `model_for_retry`:

```bash
grep -rn "model_for_retry" play-to-spring-kit/agent --include=*.py
```

Expected: no output.

- [ ] **Step 2: Commit if anything needed fixing**

Only if Step 1 uncovered a regression requiring a fix — commit that fix on
its own with a message describing what broke and why. If Step 1 is clean,
no commit needed for this task.
