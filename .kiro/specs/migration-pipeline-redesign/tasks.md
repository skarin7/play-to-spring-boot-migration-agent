# Implementation Plan: Migration Pipeline Redesign

## Overview

Implement the Play → Spring Boot migration pipeline redesign in dependency order:
Java ExtendedFixups first (foundation for deterministic coverage), then new Python modules
(fixer, clusterer, prompt builder, incremental compiler), then orchestrator wiring and
`migrate_v1_to_v2`, and finally compact skill files.

## Tasks

- [ ] 1. Add ExtendedFixups AST rules to SpringCompileFixups
  - [ ] 1.1 Implement `rewriteJavaxInjectProviderToObjectProvider` in `SpringCompileFixups.java`
    - Add static method following the existing `rewriteXxx(CompilationUnit, TransformResult)` pattern
    - Rewrite `javax.inject.Provider<T>` field/parameter types to `org.springframework.beans.factory.ObjectProvider<T>`
    - Call `ensureImport(cu, "org.springframework.beans.factory.ObjectProvider")` when applied
    - Remove `javax.inject.Provider` import when no longer referenced
    - Wire the call into `SpringCompileFixups.apply()` after the existing javax.inject block
    - _Requirements: 3.1, 3.5_

  - [ ] 1.2 Implement `rewriteProviderGetToGetObject` in `SpringCompileFixups.java`
    - Find all `MethodCallExpr` nodes where method name is `get` and scope resolves to an `ObjectProvider` field
    - Replace `.get()` with `.getObject()`
    - Wire into `SpringCompileFixups.apply()`
    - _Requirements: 3.2, 3.5_

  - [ ] 1.3 Implement `rewriteJavaxSingletonToComponent` in `SpringCompileFixups.java`
    - Find `@Singleton` annotations on the primary class declaration
    - Skip if the class already has a Spring stereotype (`@RestController`, `@Service`, etc.)
    - Replace `@Singleton` with `@Component` and call `ensureImport(cu, "org.springframework.stereotype.Component")`
    - Remove `javax.inject.Singleton` / `com.google.inject.Singleton` imports
    - Wire into `SpringCompileFixups.apply()`
    - _Requirements: 3.3, 3.5_

  - [ ]* 1.4 Write JUnit tests for ExtendedFixups rules
    - Create `SpringCompileFixupsExtendedTest.java` in `java-dev-toolkit/src/test/java/com/phenom/devtoolkit/`
    - One test per rule: Provider→ObjectProvider, get→getObject, @Singleton→@Component
    - Verify idempotence: apply twice, assert output equals single-application output (Property 10)
    - Verify Provider round-trip: file with `javax.inject.Provider<T>` and `.get()` calls has no remaining `javax.inject.Provider` after apply (Property 11)
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

- [ ] 2. Implement `compile_error_fixer.py`
  - [ ] 2.1 Create `play-to-spring-kit/scripts/compile_error_fixer.py` with `FixResult` dataclass and `CompileErrorFixer` class skeleton
    - Define `FixResult(fixed_count: int, unresolved: list[dict], det_fix_log: list[str])`
    - Define `CompileErrorFixer.__init__(self, spring_repo: Path, dry_run: bool = False)`
    - Implement `.bak` backup logic: write `<file>.bak` before any edit; delete on successful compile round
    - Implement revert logic: if subsequent compile produces `illegal start of expression` or `reached end of file` on a file just edited, restore from `.bak` and move error to `unresolved`
    - Ensure no file under `play_repo` is ever modified (validate path prefix before writing)
    - _Requirements: 4.2, 4.3, 4.4_

  - [ ] 2.2 Implement taxonomy rules E01–E12 in `CompileErrorFixer.run()`
    - E01: `cannot find symbol.*class ObjectMapper` → add `import com.fasterxml.jackson.databind.ObjectMapper;`
    - E02: `cannot find symbol.*class JsonNode` → add `import com.fasterxml.jackson.databind.JsonNode;`
    - E03: `package play\.\w+ does not exist` → remove matching Play import line
    - E04: `cannot find symbol.*method ok\(` → rewrite to `ResponseEntity.ok(...)`
    - E05: `incompatible types.*Result.*ResponseEntity` → change method return type
    - E10: `method .* is already defined` → remove duplicate method, keep Spring-idiomatic version
    - E11: `cannot find symbol.*class RestTemplate` → generate `@Bean RestTemplate` config class in spring repo
    - Append one human-readable entry to `FixResult.det_fix_log` per fix applied
    - Ensure `result.fixed_count + len(result.unresolved) == len(errors)` for every call
    - _Requirements: 4.1, 4.5, 4.6, 4.7, 4.8, 4.9, 4.10_

  - [ ]* 2.3 Write property test for `CompileErrorFixer.run()` losslessness
    - **Property 2: Fixer losslessness** — `fixed_count + len(unresolved) == len(errors)` for any input list
    - **Validates: Requirements 4.1**
    - Use `hypothesis` with a strategy generating dicts with `file`, `line`, `message` keys
    - Create `play-to-spring-kit/tests/test_compile_error_fixer.py`

  - [ ]* 2.4 Write property test for `CompileErrorFixer` never modifying Play source files
    - **Property 3: Fixer never modifies Play source files**
    - **Validates: Requirements 4.4**
    - Generate error lists with `file` paths pointing into a tmp play repo; assert no file under play_repo is modified after `run()`

  - [ ]* 2.5 Write unit tests for all 12 taxonomy rules
    - Parametrize over E01–E12 error message patterns
    - For each: assert fix applied, file content correct, `det_fix_log` populated
    - Test backup/revert: inject a bad fix, assert file reverted from `.bak`
    - _Requirements: 4.2, 4.3, 4.5–4.10_

- [ ] 3. Implement `error_clusterer.py`
  - [ ] 3.1 Create `play-to-spring-kit/scripts/error_clusterer.py` with `ErrorCluster` dataclass and `ErrorClusterer` class
    - Define `ErrorCluster(root_cause, representative, affected_files, count, suggested_fix)`
    - Implement `extract_template(message)`: strip file paths, line numbers, quoted identifiers; lowercase
    - Implement `cluster(errors)`: group by template, populate `suggested_fix` from taxonomy lookup, sort descending by `count`
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5_

  - [ ]* 3.2 Write property test for `ErrorClusterer` losslessness
    - **Property 5: Clusterer losslessness** — `sum(c.count for c in clusters) == len(errors)` for any input including empty list
    - **Validates: Requirements 5.1, 5.2**
    - Create `play-to-spring-kit/tests/test_error_clusterer.py`

  - [ ]* 3.3 Write property test for `ErrorClusterer` sort order
    - **Property 6: Clusterer sort order** — `clusters[i].count >= clusters[i+1].count` for all valid `i`
    - **Validates: Requirements 5.3**

  - [ ]* 3.4 Write property test for same-template grouping
    - **Property 7: Clusterer same-template grouping** — N errors with identical normalized template → exactly 1 cluster with `count == N`
    - **Validates: Requirements 5.4**

- [ ] 4. Checkpoint — ensure Java toolkit builds and Python fixer/clusterer tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 5. Implement `prompt_builder.py`
  - [ ] 5.1 Create `play-to-spring-kit/scripts/prompt_builder.py` with `PromptBuilder` class
    - Implement `system_prompt(spring_repo, play_repo, status_path) -> str`: static context ≤200 tokens; include repo paths and forbidden-actions rules
    - Implement `fix_prompt(clusters, spring_repo) -> str`: include at most 5 clusters; per-cluster: root_cause, representative file+line+message, suggested_fix hint if present, affected_files[1:3] if count > 1; footer forbidding Play source edits; target ≤500 tokens
    - Implement `bootstrap_prompt(play_repo, spring_repo, status_path, step1_md) -> str`: inline step1_md once; target ≤1000 tokens total
    - Do NOT include full skill markdown content in any per-fix prompt
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 6.1, 6.2, 6.3, 6.4, 6.5_

  - [ ]* 5.2 Write property test for system prompt token budget
    - **Property 8: System prompt token budget** — `system_prompt()` produces ≤200 tokens for any valid path configuration
    - **Validates: Requirements 2.1**
    - Use word-count approximation (tokens ≈ words × 1.3) or `tiktoken` if available
    - Create `play-to-spring-kit/tests/test_prompt_builder.py`

  - [ ]* 5.3 Write property test for fix prompt token budget
    - **Property 9: Fix prompt token budget** — `fix_prompt()` produces ≤500 tokens for any list of up to 5 `ErrorCluster` objects
    - **Validates: Requirements 2.2**

- [ ] 6. Implement `incremental_compiler.py`
  - [ ] 6.1 Create `play-to-spring-kit/scripts/incremental_compiler.py` with `CompileResult` dataclass and `IncrementalCompiler` class
    - Define `CompileResult(returncode, log, errors, duration_sec)`
    - Implement `_resolve_module(file) -> str | None`: parse `pom.xml` files under `spring_repo` to find the Maven module containing `file`; return `None` for single-module projects
    - Implement `compile(changed_files=None) -> CompileResult`:
      - Single-module or no `changed_files`: run `mvn -q compile`
      - Multi-module with `changed_files`: run `mvn -q compile -pl :<module> --am`
      - Fall back to full compile when module cannot be determined
      - Parse stdout/stderr with `MVN_ERROR_RE` to populate `errors`
      - Set `duration_sec` from wall-clock time
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6_

  - [ ]* 6.2 Write property test for `IncrementalCompiler` returncode ↔ errors invariant
    - **Property 14: Compiler returncode ↔ errors list** — `returncode == 0` iff `errors` is empty
    - **Validates: Requirements 7.1, 7.2**
    - Use a mock/stub `subprocess.run` that returns controlled output; generate arbitrary error lists
    - Create `play-to-spring-kit/tests/test_incremental_compiler.py`

- [ ] 7. Implement `migrate_v1_to_v2` in `migration_orchestrator.py`
  - [ ] 7.1 Add `migrate_v1_to_v2(raw: dict) -> dict` function to `migration_orchestrator.py`
    - If `raw.get("schema_version") == 2`, return `raw` unchanged
    - Set `schema_version: 2`
    - For each unit/layer entry: convert `errors_history` (list of full error dicts) to `error_fingerprints` (list of sorted `"file:line:msg"` string lists), truncated to last 5 entries
    - Add `det_fix_log: []` to each unit/layer that lacks it
    - Add `prompt_cache_key: null` to `autonomous` block if absent
    - Preserve `status`, `files_migrated`, `validate_iteration` for every unit unchanged
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6_

  - [ ]* 7.2 Write property test for `migrate_v1_to_v2` preserving unit progress
    - **Property 12: v1→v2 migration preserves unit progress** — every unit's `status`, `files_migrated`, `validate_iteration` are identical before and after migration
    - **Validates: Requirements 8.2**
    - Create `play-to-spring-kit/tests/test_migrate_v1_to_v2.py`

  - [ ]* 7.3 Write property test for fingerprint conversion bounds
    - **Property 13: v1→v2 fingerprint conversion is bounded** — for a unit with `errors_history` of length L, `error_fingerprints` has length `min(L, 5)` and each entry is a list of strings
    - **Validates: Requirements 8.3**

- [ ] 8. Wire new components into the orchestrator loop
  - [ ] 8.1 Integrate `IncrementalCompiler` into the transform-validate loop in `migration_orchestrator.py`
    - Replace direct `subprocess.run(["mvn", "compile", ...])` calls in the per-unit validate loop with `IncrementalCompiler(spring_repo).compile(changed_files=last_edited_files)`
    - Detect infrastructure errors (JDK crash, Lombok annotation processor failure) from `CompileResult.log`; exit with code 5 without marking unit failed
    - _Requirements: 7.4, 7.5, 7.6, 10.2_

  - [ ] 8.2 Integrate `CompileErrorFixer` and `ErrorClusterer` into the validate loop
    - After each compile failure, call `CompileErrorFixer(spring_repo).run(code_errors)` before dispatching to cursor-agent
    - Extend `unit["det_fix_log"]` with `fix_result.det_fix_log`
    - Only cluster and dispatch to cursor-agent when `fix_result.unresolved` is non-empty
    - Delete `.bak` files after a successful compile round
    - _Requirements: 1.1, 1.2, 1.3, 4.4, 10.1, 10.3_

  - [ ] 8.3 Integrate `PromptBuilder` and replace inline prompt construction
    - Instantiate `PromptBuilder` once per orchestrator run
    - Replace bootstrap prompt string with `prompt_builder.bootstrap_prompt(...)`
    - Replace per-fix cursor-agent prompt with `prompt_builder.fix_prompt(clusters, spring_repo)`
    - Pass `system_prompt()` as the `--system-prompt` argument to cursor-agent (sent once per session)
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 6.1–6.5_

  - [ ] 8.4 Implement error fingerprint loop detection and budget enforcement
    - After each deterministic+LLM round, compute `current_fps = sorted("file:line:msg" for each unresolved error)`
    - Compare against `unit["error_fingerprints"]`; if match found and `unit["llm_calls"] >= escalate_after_retries`, mark unit `failed` with `failure_reason = "stuck"` and continue to next unit
    - Append `current_fps` to `unit["error_fingerprints"]`, keeping only last 5 entries
    - When `total_llm_calls >= max_total_llm_calls`, mark current unit `failed` with `failure_reason = "budget_exhausted"` and exit with code 4; preserve all `"done"` units
    - _Requirements: 9.1, 9.2, 9.3_

  - [ ] 8.5 Add cross-module full-compile verification pass
    - After all units in a run are `"done"`, run `IncrementalCompiler(spring_repo).compile()` (no `changed_files`) as a full cross-module validation pass
    - Feed any errors back into the deterministic-first loop for the affected unit
    - Set `current_step = "verify"` then `"done"` only after this pass succeeds
    - Write `migration-status.json` atomically (write to `.tmp` then rename) after each unit transitions to `"done"`
    - _Requirements: 9.5, 9.6, 10.4_

  - [ ] 8.6 Call `migrate_v1_to_v2` at orchestrator startup
    - In the status load path, after reading `migration-status.json`, call `status = migrate_v1_to_v2(status)` before any other processing
    - _Requirements: 8.1–8.6_

- [ ] 9. Checkpoint — ensure all Python tests pass and orchestrator integration is coherent
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 10. Write compact skill markdown files
  - [ ] 10.1 Create `play-to-spring-kit/skills/builder-skill.compact.md`
    - Decision-tree format: IF/THEN/FORBIDDEN structure
    - Cover: check `initialize.status`, read `build.sbt` → map deps → write `pom.xml`, write `Application.java`, write `application.properties`, run `mvn -q compile`, set `initialize.status = done`
    - Target ≤700 tokens; do not include migrate-app instructions or orchestrator skill content
    - _Requirements: 2.5_

  - [ ] 10.2 Create `play-to-spring-kit/skills/orchestrator-skill.compact.md`
    - Decision-tree format covering: unit discovery, transform-validate loop, deterministic-first order, LLM escalation conditions, budget enforcement, resumability rules
    - Target ≤800 tokens
    - _Requirements: 2.5_

  - [ ] 10.3 Create `play-to-spring-kit/skills/transformer-skill.compact.md`
    - Decision-tree format covering: layer detection, AST rewrite rules summary, ExtendedFixups rules, import cleanup
    - Target ≤400 tokens
    - _Requirements: 2.5_

- [ ] 11. Final checkpoint — ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Property tests use `hypothesis` (Python) and JUnit for Java; install with `pip install hypothesis`
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation before wiring the next layer
- The Java toolkit must be rebuilt (`mvn package -DskipTests` in `java-dev-toolkit/`) after task 1 before Python integration tests can run end-to-end
