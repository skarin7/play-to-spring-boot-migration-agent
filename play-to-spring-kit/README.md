# Play-to-Spring Migration Kit

**Independent, reusable** kit to migrate any **Play Framework (Java)** repo to **Spring Boot**.

- **`scripts/migration_orchestrator.py`** is the usual entry point: it **builds `java-dev-toolkit`**, copies the JAR into **`lib/`**, runs **`setup.sh`** to **prepare the workspace** (Spring layout, Cursor skills, JAR into the play repo, **`workspace.yaml`**), then drives **layered `migrate-app` + `mvn compile`**, with optional headless **`cursor-agent`** for compile fixes. Details: **[scripts/README.md](scripts/README.md)**.
- **LLM/agent** initializes the Spring project (`pom.xml`, `Application.java`, `application.properties`) by reading the Play project.
- **CLI** does ~70% deterministic migration; **LLM/agent** fixes the rest until the build is clean.

## Quick start

### Single command (recommended, full monorepo clone)

From **this kit** directory (sibling **`java-dev-toolkit/`** is built automatically):

```bash
cd /path/to/play-to-spring-kit
python3 scripts/migration_orchestrator.py --play-repo /path/to/<play-repo>
# or relative:  --play-repo ../<play-repo>
```

The orchestrator runs **`mvn package -DskipTests`** in **`java-dev-toolkit/`**, copies **`dev-toolkit-*.jar`** into **`play-to-spring-kit/lib/`**, then runs **`setup.sh`** and the migration loop. Details and overrides (**`--skip-build-toolkit`**, **`--toolkit-root`**, **`JAVA_DEV_TOOLKIT_ROOT`**) are in **[scripts/README.md](scripts/README.md)**.

- **Requires:** Python **3.10+**, **`java`** / **`mvn`** on `PATH`; **`cursor-agent`** on `PATH` only if you use API-key mode (see **[scripts/README.md](scripts/README.md)**).
- **`migration-status.json`** must exist under the Spring repo with **`initialize.status: done`** once the builder has generated the Spring scaffold; otherwise the script exits **3** (same gate as the Cursor orchestrator skill).

### Manual JAR build (optional)

If you prefer not to use the orchestrator’s automatic build:

```bash
cd /path/to/java-dev-toolkit
mvn -q package
cp target/dev-toolkit-1.0.0.jar /path/to/play-to-spring-kit/lib/
```

Then run the orchestrator with **`--skip-build-toolkit`**, or use **`setup.sh`** / the JAR directly as below.

### Alternative — manual CLI + Cursor skills (no Python)

If you are **not** using **`migration_orchestrator.py`**, install the kit into the play repo once from the kit directory (the same step the orchestrator performs automatically), then use the JAR and skills:

```bash
cd /path/to/play-to-spring-kit
./scripts/setup.sh /path/to/<play-repo>
```

That creates **`spring-<basename>`**, copies **`dev-toolkit-1.0.0.jar`**, installs **`<play-repo>/.cursor/skills/`** and **`config/`** / **`docs/`**, and writes **`workspace.yaml`**. **Progress** lives in **`<spring-repo>/migration-status.json`** (orchestrator agent or Python script — not created by the install step alone).

Then from the **Play repo**:

```bash
cd /path/to/<play-repo>
java -jar dev-toolkit-1.0.0.jar migrate-app
```

Defaults: source = `.`, target = `../spring-<basename>`.

### Build until clean

```bash
cd ../spring-<basename> && mvn compile
```

Fix errors, re-run `mvn compile`, repeat until it passes. Use the **Builder** and **Transformer** skills in Cursor to automate this.

## Python autonomous orchestrator (CLI)

Full flags, env vars (`CURSOR_API_KEY`, model overrides, guardrails), exit codes, and overnight **`nohup`** examples: **[scripts/README.md](scripts/README.md)**.

Summary:

- **Only `--play-repo`** is required (absolute or relative to your shell cwd); by default the **dev-toolkit Maven build** and **workspace preparation** run on every invocation (idempotent). Use **`--skip-build-toolkit`** when the JAR in **`lib/`** is already up to date.
- Spring repo and default **`migration-status.json`** come from **`workspace.yaml`** or **`spring-<play-basename>`**.
- Prefer **`cd play-to-spring-kit`** then **`python3 scripts/migration_orchestrator.py --play-repo ../your-play-app`** so relative play paths match your tree; kit paths are resolved from the script location, not cwd.

## Architecture & autonomous pipeline

See **[docs/play_to_spring_migration.md](docs/play_to_spring_migration.md)** for the full architecture: orchestrator + skills + **`dev-toolkit-1.0.0.jar`**, state file, layer order, and failure handling.

**Autonomous options:**

1. **Cursor** — open the Play repo → **Agent** → skill **`play-spring-orchestrator`** → e.g. *“Execute the full play-spring-orchestrator migration loop… resume from migration-status.json if present.”* (**§2.1** in that doc.)
2. **Python CLI** — **`scripts/migration_orchestrator.py`** (this repo), same state file and layer order; optional **`cursor-agent`** for compile fixes.

## Orchestration (Cursor agent flow)

See **[docs/ORCHESTRATION.md](docs/ORCHESTRATION.md)** for the step-by-step guide.

One **orchestrator agent** runs three steps:

1. **Initialize:** Read `build.sbt` + `application.conf` → generate `pom.xml`, `Application.java`, `application.properties` in the Spring repo.
2. **Transform:** `java -jar dev-toolkit-1.0.0.jar migrate-app` (from Play repo).
3. **Validate:** `cd ../spring-<basename> && mvn compile`; fix all errors; repeat until success.

All commands are in the skills; the agent runs CLI directly.

## Requirements

- **Bash** (only if you run **`./scripts/setup.sh`** manually instead of relying on **`migration_orchestrator.py`**)
- **Maven** (for the Spring project)
- **Java 17+** (for Spring Boot 3 and the dev-toolkit JAR)
- **Python 3.10+** (for **`scripts/migration_orchestrator.py`**)
- **`cursor-agent`** on `PATH` (only if you use API-key mode with the Python orchestrator; see **[scripts/README.md](scripts/README.md)**)

## Layout after workspace preparation

```
play-to-spring-kit/                   # This kit (clone)
├── lib/                              # dev-toolkit-*.jar (orchestrator build or manual copy)
├── scripts/
│   ├── setup.sh                      # Manual kit install (optional; orchestrator runs it automatically)
│   ├── migration_orchestrator.py    # Recommended: bootstrap + layered migrate + compile
│   └── README.md
├── skills/                           # Source skill markdown (copied into play .cursor/skills/)
└── docs/

workspace/                            # Default: parent of <play-repo>
├── <play-repo>/                      # Your Play repo
│   ├── dev-toolkit-1.0.0.jar         # Copied during workspace prep
│   └── .cursor/                      # Cursor + kit reference
│       ├── skills/                   # Agent skills (play-spring-*)
│       ├── config/                   # e.g. workspace.example.yaml
│       └── docs/                     # ORCHESTRATION, play_to_spring_migration, …
├── spring-<basename>/                # Spring Boot project (layout from prep; pom etc. by agent)
│   ├── migration-status.json         # Resumable state (orchestrator agent or Python script)
│   ├── src/main/java/
│   ├── src/main/resources/
│   ├── src/test/java/
│   └── src/test/resources/
├── workspace.yaml
└── route-map.json                    # Optional placeholder
```

## Cursor Agent skills

After workspace preparation, skills live under `<play-repo>/.cursor/skills/` so Cursor discovers them:

- **play-spring-orchestrator** — the single entry point: Initialize → Transform → Validate.
- **play-spring-transformer** — run dev-toolkit CLI to migrate classes.
- **play-spring-builder** — initialize Spring project (pom.xml, Application.java, application.properties) + compile + fix loop.

## Using the kit on another repo

**Full repository (recommended):** clone this repo so **`java-dev-toolkit/`** sits next to **`play-to-spring-kit/`**, then:

```bash
cd play-to-spring-kit
python3 scripts/migration_orchestrator.py --play-repo <path-to-play-repo>
```

See **[scripts/README.md](scripts/README.md)** for **`--toolkit-root`** / **`JAVA_DEV_TOOLKIT_ROOT`** if your layout differs.

**Kit folder only:** copy **`play-to-spring-kit/`** and put **`dev-toolkit-*.jar`** in **`lib/`**, then either run the orchestrator with **`--skip-build-toolkit`** or use **`./scripts/setup.sh`** and the JAR manually as in the alternative flow above.

Use Cursor skills as needed for init / fixes.
