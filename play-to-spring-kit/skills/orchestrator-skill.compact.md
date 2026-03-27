# Orchestrator: Full Migration Workflow

## Goal
Run the full Play→Spring migration: Initialize → Transform+Validate per slice → Verify.
Track all progress in `migration-status.json` at `<spring-repo>/migration-status.json`.

## Agent Coordination

| Mechanism | What it proves |
|-----------|----------------|
| `java -jar dev-toolkit-1.0.0.jar migrate-app ...` | Toolkit (transform) ran |
| `mvn compile` + fixes | Validation happened; NOT proof builder skill was read |
| Read tool on `play-spring-builder/SKILL.md` | Builder skill was loaded |
| Read tool on `play-spring-transformer/SKILL.md` | Transformer skill was loaded |

**At migration start:** Read transformer skill before any `migrate-app`, builder skill before init and each compile-fix loop.

## migration-status.json Schema

```json
{
  "schema_version": 2,
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
      "id": "com_example_services",
      "path_prefix": "com/example/services",
      "java_file_count": 42,
      "status": "pending | in_progress | done | failed",
      "retry_count": 0, "llm_calls": 0,
      "files_migrated": 0, "files_failed": [],
      "validate_iteration": 0, "last_error_count": null,
      "failure_reason": null,
      "det_fix_log": [], "error_fingerprints": []
    }
  ],
  "layers": {
    "model":      { "status": "pending", "retry_count": 0, "llm_calls": 0, "files_migrated": 0, "files_failed": [], "validate_iteration": 0, "last_error_count": null, "failure_reason": null, "det_fix_log": [], "error_fingerprints": [] },
    "repository": { "...": "same structure" },
    "manager":    { "...": "same" },
    "service":    { "...": "same" },
    "controller": { "...": "same" },
    "other":      { "...": "same" }
  },
  "failed_layers": [],
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
    "cursor_model": "...", "cursor_model_fix": "...", "cursor_model_escalate": "...",
    "escalate_after_retries": 2,
    "max_files_per_cursor_session": 10,
    "last_errors_path": null,
    "prompt_cache_key": null
  }
}
```

## Unit Discovery

`migration_units` in status → use cached.
ELSE discover from Play `app/`:
1. Find parent **P** of a `controllers`/`controller` child
2. Units = **P's immediate subdirs** (+ P itself if has loose `*.java`)
3. Fallback: branch with ≥3 subdirs
4. Last resort: one unit for all `app/` (path_prefix = "")

Layer matching uses same `LayerDetector` rules:
| Layer | Path rule |
|-------|-----------|
| controller | `/controllers/` |
| service | `/service/` or `/services/` |
| model | `/models/` or `*Model.java` |
| manager | `/db/` |
| repository | `/repositories/` or `/dao/` |
| other | anything else |

## Execution Order

### Step 1 — Initialize
IF `initialize.status == "done"` → SKIP.
Follow **builder skill**: read `build.sbt` + `application.conf` → `pom.xml`, `Application.java`, `application.properties`. Populate `source_inventory`. Set `current_step = "transform_validate"`.

### Step 2 — Transform + Validate per slice

**Default (folder units):** Process each `migration_units` entry sorted by `path_prefix`.

#### 2a. Transform
IF unit `status == "done"` → SKIP.
```bash
java -jar dev-toolkit-1.0.0.jar migrate-app --path-prefix <path-relative-to-app>
```
With `--batch-size N` for large slices (>20 files). CLI skips already-migrated. Record `files_migrated`/`files_failed`.

**Legacy mode** (`--use-semantic-layers`): process `model → repository → manager → service → controller → other` with `--layer`.

#### 2b. Validate (deterministic-first)
1. `mvn compile` (incremental: `-pl :<module> --am` if multi-module). Increment `validate_iteration`.
2. IF clean → mark done, NEXT unit.
3. IF infra error (JDK/Lombok crash) → EXIT 5.
4. Run `CompileErrorFixer(errors)` → fixed + unresolved.
5. IF all fixed → re-compile (step 1).
6. Cluster unresolved → `PromptBuilder.fix_prompt()`.
7. cursor-agent with cached `system_prompt` + `fix_prompt`.
8. Re-compile, increment `llm_calls`.
9. **Stuck detection:** compute error fingerprints (sorted "file:line:msg"). Keep last 5 in `error_fingerprints`. IF current == previous AND `llm_calls ≥ escalate_after_retries` → mark `failed("stuck")`.
10. **Budget:** IF `total_llm_calls ≥ max_total_llm_calls` → mark `failed("budget_exhausted")`, EXIT 4.

### Step 3 — Verify
After all units done, full `mvn compile` (no `-pl`). Feed errors back into deterministic-first loop.
Then run **builder skill Step 3**: compare `source_inventory` vs Spring counts → `migration_verification`.
Set `current_step = "done"` only after verification recorded.

## Resumability
- Read `migration-status.json` on start
- Skip units/layers already `"done"`
- Resume from first incomplete
- Atomic write after each unit completes

## Halt Conditions
- Initialize: `build.sbt` / `application.conf` missing → ask user
- Transform: only halt if JAR/Java missing; partial → proceed to validate
- Validation: escalate if error count not decreased for 3 iterations

FORBIDDEN: re-process done units, skip deterministic fixer, inline full skill markdown in prompts
