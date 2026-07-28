# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo does

Upgrades a Play Framework (Java) application to Spring Boot. Two engines exist side by side:

- **LangGraph engine** (`play-to-spring-kit/agent/`) — the default, a LangGraph state-graph agent. Deterministic steps (JAR transforms, `mvn compile`, config/route diffing) are plain graph nodes; LLM calls are bounded, path-jailed agent nodes whose edits are always verified by the next deterministic node, never by the LLM's own self-report. OpenRouter-backed.
- **Legacy engine** (`play-to-spring-kit/scripts/legacy/migration_orchestrator.py`) — Cursor-based (`cursor-agent` CLI), kept working via `--engine legacy` during the transition. Do not extend it; new work goes into the LangGraph engine.

A separate Java CLI JAR (`java-dev-toolkit/`) performs the actual bulk AST-style source transforms (`migrate-app`, single-file `transform`, etc.) and is invoked as a subprocess by both engines.

Full architecture/CLI/env-var reference: `docs/langgraph-engine.md`. Legacy engine reference: `play-to-spring-kit/scripts/README.md`.

## Commands

### Running the migration

```bash
./start_upgrade.sh --play-repo /path/to/your-play-app          # langgraph engine (default)
./start_upgrade.sh --engine legacy --play-repo /path/to/app     # legacy engine
```

`start_upgrade.sh` only handles environment setup — creates/syncs the venv at `play-to-spring-kit/.venv` (`requirements-venv.txt` + `requirements-agent.txt` for langgraph), pins JDK 17+ (via `jenv` or macOS `java_home`) if `JAVA_HOME` is unset/Java 8, and picks the engine (`--engine` flag or `MIGRATION_ENGINE` env). It does **not** build `java-dev-toolkit` or run `setup.sh` itself — that happens inside the engine's own bootstrap step (`agent/nodes/bootstrap.py` → `agent/tools/setup_ops.py` for langgraph; the orchestrator's own setup steps for legacy).

Running the langgraph engine directly (bypassing the wrapper's venv/JDK setup), from `play-to-spring-kit/`:

```bash
.venv/bin/python -m agent --spring-repo /path/to/spring-repo --play-repo /path/to/play-repo
```

`OPENROUTER_API_KEY` is required for LLM rounds (bootstrap, compile-fix, routes, config-mapping, runtime-wiring); deterministic fixers still run without it, and LLM-gated phases skip gracefully. `.env` at repo root loads automatically (`agent/config.py` calls `load_dotenv()` at import).

### Python tests (LangGraph engine)

```bash
cd play-to-spring-kit
.venv/bin/python -m pytest agent/tests -q                              # full suite
.venv/bin/python -m pytest agent/tests/test_context_budget.py -v                          # one file
.venv/bin/python -m pytest agent/tests/test_context_budget.py::test_name -v                # one test
```

No pytest.ini/conftest.py — plain pytest against `agent/tests/`. There is no configured linter for the Python code in this repo (no ruff/flake8/pyproject config) — match existing style rather than introducing tooling.

### Java toolkit (`java-dev-toolkit/`)

```bash
cd java-dev-toolkit
mvn clean package     # builds target/dev-toolkit-1.0.0.jar (shaded)
mvn test              # JUnit 4 + Mockito tests under src/test/java
```

## Architecture (LangGraph engine, `play-to-spring-kit/agent/`)

- **`graph.py`** builds the `StateGraph`; node builders live under **`nodes/`** (one module per phase: `bootstrap.py`, `slice_pipeline.py`, `fix_loop.py`, `routes.py`, `config_mapping.py`, `boot.py`, `human_gate.py`, plus shared budget/routing logic in `common.py`). `nodes/` exists because `graph.py` itself grew past ~1000 lines before being split out — keep that split when adding phases rather than growing `graph.py` again.
- **`agents/`** holds the actual LLM call sites, one per phase (`bootstrap.py`, `compile_fix.py`, `routes.py`, `config_mapping.py`, `runtime_wiring.py`). Each agent builds a prompt, calls `llm.run_tool_loop`, and returns edited files + a `ToolLoopResult` for the caller (a `nodes/` graph node) to verify deterministically.
- **`llm.py`** is the shared tool-call loop (`run_tool_loop`) every agent goes through: model ↔ tool-call round-trip capped by `max_agent_tool_calls`, plus a token-based context-window budget (`config.max_agent_context_tokens`) that auto-compacts the transcript (headless) or asks once via `interrupt()` per round (`--interactive`) when a round's input tokens cross the threshold (`compact_messages`). This is the one place to touch to change tool-loop behavior for all five agents at once.
- **`config.py`** (`AgentConfig`) centralizes every guardrail/env var (`MAX_TOTAL_LLM_CALLS`, `MAX_RETRIES_PER_LAYER`, `MAX_AGENT_TOOL_CALLS`, `MAX_AGENT_CONTEXT_TOKENS`, etc.) — new tunables should follow its existing `_env_int(name, default)` + dataclass `field(default_factory=...)` pattern, not ad-hoc `os.environ` reads elsewhere.
- **Guardrail precedence**: global LLM call budget (`MAX_TOTAL_LLM_CALLS`) is always checked before a phase's own local attempt cap, and always aborts the whole run (`run_outcome="budget_exhausted"`, exit 4) — the one guardrail with this precedence everywhere. Enforced by `nodes/common.py`'s `phase_budget_decision` (shared by routes/config_mapping/runtime_wiring) and `guards.py` (the compile-fix loop).
- **Resumability**: `SqliteSaver` at `<spring-repo>/.migration/langgraph-checkpoints.sqlite`, keyed by `thread_id = sha1(spring_repo path)`. Rerunning the same command resumes from the last checkpoint.
- **`--interactive` / human-in-the-loop**: implemented via LangGraph's `interrupt()`/`Command(resume=...)`. Headless mode (`config.headless=True`, the default) never calls `interrupt()` at all — this is required so every test compiles the graph without a checkpointer. Any new interactive gate must preserve that headless-never-interrupts branch. Interrupt-then-resume re-executes the containing node function from the top; only the `interrupt()` call itself is replayed from cache, not the model/tool calls before it (an accepted tradeoff for `run_tool_loop`'s context-budget interrupt — see `docs/superpowers/specs/2026-07-26-context-window-budgeting-design.md`).
- **`tools/fs.py`**'s `FsJail` path-jails every LLM file edit to the spring/play repo roots — LLM agents never get an unrestricted filesystem tool.
- Verification is always deterministic and separate from the LLM: an agent's edits are checked by the next graph node (`mvn compile`, boot + curl, config-key diff), never by asking the model whether it succeeded.

## Working conventions specific to this repo

- Design specs live in `docs/superpowers/specs/`, implementation plans in `docs/superpowers/plans/` — check these before assuming a feature's intended behavior isn't documented.
- When a plan/spec references specific line numbers or call sites, re-verify against current code before trusting them — this codebase moves fast across milestones (M1-M5 in the LangGraph rewrite so far).
- Commit hygiene: this repo tends to carry unrelated in-progress WIP across several files at once (docs, scripts) between sessions. Stage exact files by path, never `git add -A`/`.`, and check `git status` before committing to avoid sweeping unrelated WIP into a task-scoped commit.
