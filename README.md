# Play Framework (Java) → Spring Boot

This project helps you **upgrade any Play Framework application written in Java** to **Spring Boot**. The default engine is a **LangGraph** state-graph agent (`play-to-spring-kit/agent/`): deterministic steps (JAR transforms, `mvn compile`, config/route diffing) are plain graph nodes, and LLM calls are bounded, path-jailed agent nodes whose edits are always verified by the next deterministic node — never by the LLM's own self-report. A legacy **Cursor**-based orchestrator (`scripts/legacy/migration_orchestrator.py`, `cursor-agent` CLI) is still available via `--engine legacy` during the transition.

A **Java CLI JAR** (`java-dev-toolkit/`) performs bulk AST-style transforms (`migrate-app`, single-file `transform`, and related commands) for both engines. Full architecture, CLI flags, env vars, resumability, and `--interactive` human-in-the-loop mode: **[docs/langgraph-engine.md](docs/langgraph-engine.md)**. Legacy engine details: **[play-to-spring-kit/scripts/README.md](play-to-spring-kit/scripts/README.md)**.

## What triggers the automation

Automation is **not** implicit (nothing runs on clone or on folder open). You start it explicitly by running **`start_upgrade.sh`**, which picks the migration engine (langgraph by default, legacy on request) and hands off to it; the engine itself builds **`java-dev-toolkit`**, refreshes **`play-to-spring-kit/lib/`**, and runs **`setup.sh`** before driving the migration (for langgraph, this is the bootstrap graph node; for legacy, the orchestrator's own setup steps).

**Recommended (full repo clone):** from the **repository root**, copy the env template and set your key, then run:

```bash
chmod +x start_upgrade.sh   # once per clone
cp .env.example .env && $EDITOR .env   # set OPENROUTER_API_KEY at minimum
./start_upgrade.sh --play-repo /path/to/your-play-app --spring-repo /path/to/your-spring-repo
```

`.env` loads automatically (`agent/config.py` calls `load_dotenv()` at import) — no manual `export` needed for the langgraph engine. `OPENROUTER_API_KEY` is required for LLM rounds (bootstrap, compile-fix, routes, config-mapping, runtime-wiring); deterministic fixers still run without it, and LLM-gated phases skip gracefully instead of crashing. See **[.env.example](.env.example)** for the full variable list, and **[docs/langgraph-engine.md](docs/langgraph-engine.md#environment-variables)** for defaults and guardrail semantics.

[`start_upgrade.sh`](start_upgrade.sh) runs the engine with a **dedicated venv** at **`play-to-spring-kit/.venv`**: it creates that directory on first use, then **`pip install -r play-to-spring-kit/scripts/requirements-venv.txt`** (includes optional **`pyhocon`** for **`--export-play-conf`**). No system **`--break-system-packages`** is required. Set **`MIGRATION_SKIP_VENV_SYNC=1`** to skip the pip step (reuse an already-provisioned `.venv`). It also picks **JDK 17+** when **`JAVA_HOME`** is unset or is Java 8 (**`jenv`** 17 or macOS **`java_home`**), and if **`MIGRATION_WORKSPACE`** is set and you omit **`--workspace`**, prepends **`--workspace`** (after **`mkdir -p`**). Use **`PLAY_REPO`** when you omit **`--play-repo`** on the CLI. All other flags pass through unchanged.

**Legacy engine** (Cursor-based, `cursor-agent` CLI for compile fixes): pass `--engine legacy` or set `MIGRATION_ENGINE=legacy`, and export `CURSOR_API_KEY` instead of `OPENROUTER_API_KEY`. See **[play-to-spring-kit/scripts/README.md](play-to-spring-kit/scripts/README.md)**.

**Running the langgraph engine directly** (bypassing `start_upgrade.sh`'s venv/JDK setup), from **`play-to-spring-kit/`**:

```bash
.venv/bin/python -m agent --spring-repo /path/to/spring-repo --play-repo /path/to/play-repo
```

For **`--export-play-conf`** without **`start_upgrade.sh`**, use a venv or install **`pyhocon`** yourself (see kit **[scripts/README.md](play-to-spring-kit/scripts/README.md)**).

You do **not** need to `cd` into **`scripts/`**; run the wrapper from the repo root or invoke the Python file with a path as above. **`migration-status.json`** must already have **`initialize.status: done`** in the Spring project before the transform/compile loop runs; until then the orchestrator exits with code **3** (see the kit scripts README).

## What lives where

| Directory | Role | Details |
|-----------|------|---------|
| **`play-to-spring-kit/agent/`** | LangGraph engine (default): graph nodes, LLM agents, deterministic tools. | **[docs/langgraph-engine.md](docs/langgraph-engine.md)** |
| **`play-to-spring-kit/`** | Cursor skills, workspace setup, legacy Python orchestrator, and legacy migration docs (orchestrator flow, architecture, `migration-status.json`, optional `cursor-agent`). | **[play-to-spring-kit/README.md](play-to-spring-kit/README.md)** — also **[play-to-spring-kit/scripts/README.md](play-to-spring-kit/scripts/README.md)** for orchestrator flags and env vars. |
| **`java-dev-toolkit/`** | Maven project that builds **`dev-toolkit-*.jar`**: Play → Spring migration commands plus other Java utilities. **`migration_orchestrator.py`** builds it and copies the JAR into **`play-to-spring-kit/lib/`** by default (see kit **[scripts/README.md](play-to-spring-kit/scripts/README.md)**). | **[java-dev-toolkit/README.md](java-dev-toolkit/README.md)** |

For kit placement when it is not colocated with your Play repo, see **[play-to-spring-kit/LOCATION.md](play-to-spring-kit/LOCATION.md)**.

## Deeper reading

- LangGraph engine (default) — architecture, CLI/env-var reference, resumability, `--interactive`: **[docs/langgraph-engine.md](docs/langgraph-engine.md)**
- Recurring Play→Spring compile/runtime-wiring fixes reference: **[docs/compile-fixes-for-toolkit.md](docs/compile-fixes-for-toolkit.md)**

**Legacy engine** (all under `play-to-spring-kit/`):

- Architecture and pipeline: **[play-to-spring-kit/docs/legacy/play_to_spring_migration.md](play-to-spring-kit/docs/legacy/play_to_spring_migration.md)**  
- Step-by-step Cursor agent flow: **[play-to-spring-kit/docs/legacy/ORCHESTRATION.md](play-to-spring-kit/docs/legacy/ORCHESTRATION.md)**  

Clone or share this repository as a single bundle so the JAR you build matches the kit and skills version you are using.
