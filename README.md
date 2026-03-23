# Play Framework (Java) → Spring Boot

This project helps you **upgrade any Play Framework application written in Java** to **Spring Boot**, using **Cursor** for orchestration and fixes: agent skills in the editor, and—where you enable it—the **`cursor-agent`** CLI for scripted compile-fix loops alongside deterministic tooling.

A **Java CLI JAR** performs bulk AST-style transforms (`migrate-app`, single-file `transform`, and related commands). **Cursor** drives initialization of the Spring project, layered migration, and iterating until `mvn compile` is clean. How that fits together, end-to-end options, and requirements are documented under each component below—start there rather than duplicating detail here.

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
