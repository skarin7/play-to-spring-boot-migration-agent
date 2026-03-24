# Play Framework (Java) → Spring Boot

This project helps you **upgrade any Play Framework application written in Java** to **Spring Boot**, using **Cursor** for orchestration and fixes: agent skills in the editor, and—where you enable it—the **`cursor-agent`** CLI for scripted compile-fix loops alongside deterministic tooling.

A **Java CLI JAR** performs bulk AST-style transforms (`migrate-app`, single-file `transform`, and related commands). **Cursor** drives initialization of the Spring project, layered migration, and iterating until `mvn compile` is clean. How that fits together, end-to-end options, and requirements are documented under each component below—start there rather than duplicating detail here.

## What triggers the automation

Automation is **not** implicit (nothing runs on clone or on folder open). You start it explicitly by running the **migration orchestrator**, which builds **`java-dev-toolkit`**, refreshes **`play-to-spring-kit/lib/`**, runs **`setup.sh`**, then performs layered **`migrate-app`** and **`mvn compile`** (and optional **`cursor-agent`** fixes) as described in **[play-to-spring-kit/scripts/README.md](play-to-spring-kit/scripts/README.md)**.

**Recommended (full repo clone):** from the **repository root**, run:

```bash
chmod +x start_upgrade.sh   # once per clone
./start_upgrade.sh --play-repo /path/to/your-play-app
```

[`start_upgrade.sh`](start_upgrade.sh) runs the orchestrator with a **dedicated venv** at **`play-to-spring-kit/.venv`**: it creates that directory on first use, then **`pip install -r play-to-spring-kit/scripts/requirements-venv.txt`** (includes optional **`pyhocon`** for **`--export-play-conf`**). No system **`--break-system-packages`** is required. Set **`MIGRATION_SKIP_VENV_SYNC=1`** to skip the pip step (reuse an already-provisioned `.venv`). All orchestrator flags (e.g. **`--help`**, **`--skip-build-toolkit`**, **`--workspace`**) pass through unchanged.

**Equivalent (system Python):** from **`play-to-spring-kit/`**:

```bash
python3 scripts/migration_orchestrator.py --play-repo /path/to/your-play-app
```

For **`--export-play-conf`** without **`start_upgrade.sh`**, use a venv or install **`pyhocon`** yourself (see kit **[scripts/README.md](play-to-spring-kit/scripts/README.md)**).

You do **not** need to `cd` into **`scripts/`**; run the wrapper from the repo root or invoke the Python file with a path as above. **`migration-status.json`** must already have **`initialize.status: done`** in the Spring project before the transform/compile loop runs; until then the orchestrator exits with code **3** (see the kit scripts README).

## What lives where

| Directory | Role | Details |
|-----------|------|---------|
| **`play-to-spring-kit/`** | Cursor skills, workspace setup, Python orchestrator, and migration docs (orchestrator flow, architecture, `migration-status.json`, optional `cursor-agent`). | **[play-to-spring-kit/README.md](play-to-spring-kit/README.md)** — also **[play-to-spring-kit/scripts/README.md](play-to-spring-kit/scripts/README.md)** for orchestrator flags and env vars. |
| **`java-dev-toolkit/`** | Maven project that builds **`dev-toolkit-*.jar`**: Play → Spring migration commands plus other Java utilities. **`migration_orchestrator.py`** builds it and copies the JAR into **`play-to-spring-kit/lib/`** by default (see kit **[scripts/README.md](play-to-spring-kit/scripts/README.md)**). | **[java-dev-toolkit/README.md](java-dev-toolkit/README.md)** |

For kit placement when it is not colocated with your Play repo, see **[play-to-spring-kit/LOCATION.md](play-to-spring-kit/LOCATION.md)**.

## Deeper reading (all under `play-to-spring-kit/`)

- Architecture and pipeline: **[play-to-spring-kit/docs/play_to_spring_migration.md](play-to-spring-kit/docs/play_to_spring_migration.md)**  
- Step-by-step Cursor agent flow: **[play-to-spring-kit/docs/ORCHESTRATION.md](play-to-spring-kit/docs/ORCHESTRATION.md)**  

Clone or share this repository as a single bundle so the JAR you build matches the kit and skills version you are using.
