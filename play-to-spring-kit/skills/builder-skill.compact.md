# Builder: Init Spring Project & Compile-Fix

## Goal
1. **Initialize** — generate `pom.xml`, `Application.java`, `application.properties` from Play project
2. **Compile & fix** — `mvn compile` loop after each layer transform until clean
3. **Verify** — migration completeness check against `source_inventory`

Update `migration-status.json` after each action so agents can resume.

## Source Inventory → `migration-status.json`

After initialize, populate `source_inventory`:
```bash
find app -name '*.java' -type f | wc -l        # → source_inventory.total_java_files
find app -name '*.java' -type f                 # classify per LayerDetector rules → by_layer
```

Layer rules (same as dev-toolkit `LayerDetector`):
| Layer | Path rule |
|-------|-----------|
| controller | `/controllers/` |
| service | `/service/` or `/services/` |
| model | `/models/` or `*Model.java` |
| manager | `/db/` |
| repository | `/repositories/` or `/dao/` |
| other | anything else |

Spring side: `find src/main/java -name '*.java' -type f | wc -l` → `migration_verification.spring_java_total`

## Step 1: Initialize

IF initialize.status == done → SKIP

1. Read existing `pom.xml` in Spring repo (do NOT regenerate — scaffolded by start.spring.io)
2. Read `build.sbt` → add project deps to existing `<dependencies>`:
   - MongoDB → `spring-boot-starter-data-mongodb`
   - Neo4j → `spring-boot-starter-data-neo4j`
   - Guice → remove (Spring DI built-in)
3. Read `conf/application.conf` → append to `application.properties`:
   - `mongodb.uri` → `spring.data.mongodb.uri`
   - `play.server.http.port` → `server.port`
   - Skip `play.http.secret.key`, `akka.*`
4. Verify `Application.java` package matches `base_package` from `workspace.yaml`
5. `mvn -q compile` → fix until clean
6. Set `initialize.status = "done"`, `*_generated = true`, populate `source_inventory`

FORBIDDEN: regenerate pom.xml from scratch, run migrate-app

## Step 2: Compile-Fix Loop (per layer/batch)

Called by orchestrator after each transform. Loop:

1. `mvn compile`. Increment `layers.<layer>.validate_iteration`
2. IF errors: parse count → `last_error_count`, fix in Spring only
   - Cannot find symbol → add import or fix type
   - Missing method → Play→Spring mapping
   - Missing file → read Play, create Spring version
3. Re-compile
4. Stuck: if error count not decreased for 3 iterations → escalate
5. Repeat until `mvn compile` exits 0

Update layer status in `migration-status.json`.

## Step 3: Migration Verification

When `current_step = "verify"`:
1. Load `source_inventory`
2. Count Spring `*.java` under `src/main/java` → `migration_verification.spring_java_total`
3. Per-layer: `play_expected` from `source_inventory.by_layer`, `spring_actual` from counting
4. Compute `delta = spring_actual - play_expected`
5. Set `migration_verification.status`: `passed` / `needs_review` / `failed`
6. Set `current_step = "done"`

## migration-status.json Schema

```json
{
  "current_step": "initialize | transform_validate | verify | done",
  "initialize": {
    "status": "pending | done | failed",
    "pom_generated": false,
    "application_java_generated": false,
    "application_properties_generated": false,
    "error": null
  },
  "source_inventory": {
    "captured_at": "ISO-8601",
    "play_java_root": "app",
    "total_java_files": 0,
    "by_layer": { "model": 0, "repository": 0, "manager": 0, "service": 0, "controller": 0, "other": 0 }
  },
  "migration_units": [
    {
      "id": "com_example_controllers",
      "path_prefix": "com/example/controllers",
      "java_file_count": 0,
      "status": "pending",
      "files_migrated": 0, "files_failed": [],
      "validate_iteration": 0, "last_error_count": null,
      "det_fix_log": [], "error_fingerprints": []
    }
  ],
  "layers": {
    "model":      { "status": "pending", "files_migrated": 0, "files_failed": [], "validate_iteration": 0, "last_error_count": null },
    "repository": { "...": "same" },
    "manager":    { "...": "same" },
    "service":    { "...": "same" },
    "controller": { "...": "same" },
    "other":      { "...": "same" }
  },
  "migration_verification": {
    "status": "pending | passed | failed | needs_review",
    "checked_at": null, "spring_java_total": null, "notes": null,
    "layer_comparison": {
      "model": { "play_expected": null, "spring_actual": null, "delta": null },
      "...": "same for each layer"
    }
  },
  "autonomous": {
    "total_llm_calls": 0, "max_total_llm_calls": 50,
    "prompt_cache_key": null
  },
  "schema_version": 2
}
```

## Agent Verification

- Read this file explicitly at start of init and before compile-fix loops
- Running `mvn compile` alone does NOT prove this skill was followed
- For transform behavior, see `play-spring-transformer` skill
