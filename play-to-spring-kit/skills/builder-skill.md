---
name: play-spring-builder
description: Compile Spring project and fix errors until build passes. Use after transforming files.
---

# Builder Skill (Validation sub-agent)

## Goal

You handle two responsibilities:

1. **Initialize the Spring project** — generate `pom.xml`, `Application.java`, and `application.properties` by reading the Play project's `build.sbt` and `conf/application.conf`.
2. **Compile and fix until clean** — run `mvn compile` and fix every error until the build succeeds. This runs **after each layer's transform** (or after each batch within a layer), not once at the end.

Update `migration-status.json` in the Spring repo after each action so the orchestrator can track progress and resume.

## Source file counts → `migration-status.json`

Use the **Play** repo root as working directory unless noted.

### Total Java files under `app/` (recursive)

```bash
find app -name '*.java' -type f | wc -l
```

Store the number in **`source_inventory.total_java_files`**. Set **`source_inventory.play_java_root`** to `app` (or your actual Java sources root under the Play project).

### Per-layer counts (match dev-toolkit `LayerDetector`)

Classify each path from:

```bash
find app -name '*.java' -type f
```

using the **orchestrator skill** table (`/controllers/`, `/service/` or `/services/`, `/models/` or `*Model.java`, `/db/`, `/repositories/` or `/dao/`, else **other**). A file must be counted in **exactly one** layer; apply rules in a fixed order (e.g. controller → service → model → manager → repository → other) so overlaps resolve consistently.

Write counts into **`source_inventory.by_layer`**. Set **`source_inventory.captured_at`** to an ISO-8601 timestamp.

**When:** Prefer capturing right after **initialize** succeeds (same agent session), before the first `migrate-app`.

### Spring side (for verification)

From the **Spring** repo:

```bash
find src/main/java -name '*.java' -type f | wc -l
```

Use this for **`migration_verification.spring_java_total`**. For **`layer_comparison.<layer>.spring_actual`**, count Spring `*.java` files whose path matches the **same** segment rules as Play (mirrored package layout under `src/main/java/...`).

## Step 1: Initialize Spring project

`setup.sh` bootstraps the Spring repo from [start.spring.io](https://start.spring.io) — a known-good
`pom.xml` (Spring Boot 3.2.5, Java 17, `spring-boot-starter-web`) and a minimal `Application.java`
are already present. Your job is to add the project-specific dependencies and config.

1. **Check what's already there.** Read `pom.xml` in the Spring repo. It will have the parent BOM,
   `spring-boot-starter-web`, and `spring-boot-maven-plugin` already wired. Do not regenerate it
   from scratch.

2. **Read `build.sbt`** in the Play repo. For each dependency not already covered by the web starter,
   add the Maven equivalent inside the existing `<dependencies>` block:
   - MongoDB driver → `spring-boot-starter-data-mongodb`
   - Neo4j driver → `spring-boot-starter-data-neo4j` or `neo4j-java-driver`
   - Guice → remove (Spring DI replaces it)
   - Any other third-party lib → find the Maven coordinates and add with explicit version

3. **Read `conf/application.conf`** in the Play repo. Append project-specific keys to
   `src/main/resources/application.properties` (the file already exists from the scaffold):
   - `mongodb.uri` → `spring.data.mongodb.uri`
   - `play.server.http.port` → `server.port`
   - Skip Play-internal keys (`play.http.secret.key`, `akka.*`, etc.)

4. **Verify `Application.java`** is at `src/main/java/<base-package-path>/Application.java`.
   The scaffold places it at the correct package. If `base_package` in `workspace.yaml` differs
   from the generated package, move the file and update the `package` declaration.

5. **Run `mvn -q compile`** from the Spring repo. Fix any errors (missing deps, wrong groupId,
   etc.) until it exits 0.

**After:** Update `migration-status.json` → `initialize.status = "done"`, set each `*_generated`
flag to `true`, and populate **`source_inventory`** (see above) if not already present.

## Step 2: Compile and fix until clean (per layer / per batch)

The orchestrator calls you after each layer's transform (or after each batch within a large layer). Your job is to get `mvn compile` to pass before the orchestrator moves on.

```bash
cd <spring-repo> && mvn compile
```

### Loop until clean

1. Run `mvn compile`. Increment iteration number for the current layer in `migration-status.json` → `layers.<layer>.validate_iteration`.
2. If there are errors:
   - Parse the error count and details (file, line, message).
   - Update `layers.<layer>.last_error_count`.
   - **Cannot find symbol** → add missing import or fix type; add missing dependency to `pom.xml` if needed.
   - **Method not found / signature mismatch** → apply Play→Spring mapping (e.g. `Result` → `ResponseEntity`, Play APIs → Spring equivalents).
   - **Missing file** (referenced class doesn't exist in Spring) → read the Play source and manually create the Spring version.
   - Edit only the **Spring** project; do not change the Play repo.
3. Run `mvn compile` again.
4. **Stuck detection:** If error count hasn't decreased for 3 consecutive iterations, escalate to the user with the list of remaining errors.
5. Repeat until `mvn compile` exits 0.

**After:** Update the layer's status in `migration-status.json`.

### Error budget per batch

When the orchestrator uses `--batch-size`, each batch produces roughly 5–15 compilation errors. This is manageable. If a full layer was transformed at once and produces > 20 errors, focus on the most impactful errors first (missing dependencies in `pom.xml`, then import fixes, then method signature changes).

## Step 3: Migration completeness verification (after full compile is green)

Run when the **orchestrator** sets `current_step` to **`verify`**, or immediately before marking migration **`done`**.

1. **Load** `source_inventory` from `migration-status.json`. If missing, compute it now (same rules as above) and write it back.
2. **Count Spring** `*.java` under `src/main/java` (recursive) → `migration_verification.spring_java_total`.
3. **Per layer:** For each layer key, set `migration_verification.layer_comparison.<layer>.play_expected` from `source_inventory.by_layer.<layer>`, and `spring_actual` from counting Spring files using the **same path rules** as `LayerDetector`.
4. **Deltas:** `delta = spring_actual - play_expected` (document sign convention in notes). Small negative deltas can mean Play-only files not copied (e.g. `Module.java`, `Filters.java`); large negative deltas suggest **unmigrated** sources — investigate with `find` and path lists.
5. **Optional:** Compare `sum(layers.*.files_migrated)` to transformation expectations; CLI numbers are “processed in runs” and may not equal final file count — prefer **inventory vs Spring counts** for completeness.
6. Set **`migration_verification.status`**: `passed` if explainable and within project tolerance; `needs_review` if gaps are understood (stubs, deleted glue); `failed` if unmigrated Play files are likely.
7. Set **`migration_verification.checked_at`** (ISO-8601) and short **`notes`**.
8. Set **`current_step`** to **`done`** (orchestrator).

## Success criteria

- `pom.xml`, `Application.java`, and `application.properties` exist with accurate dependencies and config from the Play project.
- `mvn compile` completes with no errors after each layer/batch.
- `migration-status.json` reflects all steps completed.
- **`source_inventory`** captured from Play `app/**/*.java` and **`migration_verification`** filled after the final successful compile (orchestrator Step 3).

## Agent usage and verification

These skills are **markdown instructions** for the agent. They are **not** automatically chained: following the **orchestrator** prompt alone does not guarantee the agent **read** `play-spring-builder` or `play-spring-transformer` from disk.

### How to ensure this skill actually runs

- **User:** Ask explicitly, e.g. *“Read and follow `.cursor/skills/play-spring-builder/SKILL.md` for init and for every `mvn compile` loop.”*
- **Orchestrator agent:** When performing initialize + validate steps, **use the Read tool** on this file (`play-spring-builder/SKILL.md`) at the start of init and before fixing compile errors, then follow it step-by-step.

### How to tell if this skill was used (vs. ad hoc fixes)

- **Strong signal:** The agent states it read this skill or quotes concrete steps from it (e.g. `build.sbt` → `pom.xml` mapping, `validate_iteration` in `migration-status.json`).
- **Weak signal:** Only `mvn compile` appears in the transcript with no reference to builder steps—could be ad hoc.
- **Not proof of this skill:** A green build alone; could be manual edits without following this document.

### Relationship to other pieces

- **dev-toolkit JAR** (`migrate-app`) handles **transform**; it is **not** the same as loading the **play-spring-transformer** skill file.
- **Subagents** (if your Cursor setup uses them) show as **separate agent runs** in the UI. A single long thread with no delegated tasks usually means **no subagents** were used.
- **Orchestrator** text may be **inlined in the user message**; that still does not prove **builder** or **transformer** skill files were opened—check for an explicit Read of those paths.
