# Requirements Document

## Introduction

This document specifies the requirements for the Play Framework → Spring Boot autonomous migration
pipeline redesign. The redesign targets four concrete goals: push deterministic (no-LLM) coverage
from ~70% to 90%+, reduce LLM token usage by ~70%, retain cursor-agent as the LLM executor but
invoke it only for genuinely ambiguous cases, and maintain full backward compatibility with existing
`migration-status.json` files and resumability semantics.

The system consists of five new Python components (`CompileErrorFixer`, `ErrorClusterer`,
`PromptBuilder`, `IncrementalCompiler`, `migrate_v1_to_v2`), two new Java AST rewrite rules in
`SpringCompileFixups`, and compact replacements for the existing skill markdown files.

---

## Glossary

- **Pipeline**: The full Play → Spring migration orchestrator (`migration_orchestrator.py`) and all
  components it invokes.
- **Orchestrator**: `migration_orchestrator.py` — the top-level coordinator.
- **Transformer**: The Java dev-toolkit JAR (`PlayToSpringTransformer` + `SpringCompileFixups`).
- **ExtendedFixups**: New AST rewrite rules added to `SpringCompileFixups` in this redesign.
- **Fixer**: `CompileErrorFixer` — the Python taxonomy-driven deterministic rule engine.
- **Clusterer**: `ErrorClusterer` — groups compile errors by root cause before LLM dispatch.
- **PromptBuilder**: `PromptBuilder` — constructs minimal, structured prompts for cursor-agent.
- **Compiler**: `IncrementalCompiler` — wraps `mvn compile` with optional module targeting.
- **Migrator**: `migrate_v1_to_v2` — upgrades v1 `migration-status.json` to v2 schema.
- **SkillFiles**: Compact markdown decision-tree files replacing the existing prose skill files.
- **MigrationUnit**: A folder-scoped batch of Java files processed together (one `path_prefix`).
- **ErrorFingerprint**: A sorted list of `"file:line:msg"` strings representing one compile round's errors.
- **Taxonomy**: The table of 12 known error patterns (E01–E12) with deterministic fixes.
- **cursor-agent**: The LLM executor CLI; invoked only when deterministic fixes are exhausted.
- **v1 status file**: A `migration-status.json` written by the current (pre-redesign) pipeline.
- **v2 status file**: A `migration-status.json` conforming to the new schema (`schema_version: 2`).

---

## Requirements

### Requirement 1: Deterministic Coverage Target

**User Story:** As a migration operator, I want the pipeline to resolve 90%+ of compile errors
without LLM calls, so that migrations are fast, reproducible, and cost-effective.

#### Acceptance Criteria

1. WHEN the pipeline processes a Play project whose compile errors all match taxonomy entries E01–E12,
   THE Orchestrator SHALL complete the migration without invoking cursor-agent.
2. WHEN the pipeline processes a Play project, THE Pipeline SHALL resolve at least 90% of
   post-transform compile errors through the deterministic layer (Transformer + Fixer) before
   any LLM call is made.
3. WHEN the Fixer applies a taxonomy rule to a compile error, THE Fixer SHALL mark that error as
   resolved and exclude it from the LLM prompt.

---

### Requirement 2: LLM Token Reduction

**User Story:** As a migration operator, I want LLM token usage reduced by ~70%, so that
migrations are cheaper and faster.

#### Acceptance Criteria

1. THE PromptBuilder SHALL produce a system prompt of at most 200 tokens.
2. WHEN PromptBuilder.fix_prompt() is called with up to 5 error clusters, THE PromptBuilder
   SHALL produce a prompt of at most 500 tokens.
3. WHEN PromptBuilder.bootstrap_prompt() is called, THE PromptBuilder SHALL produce a prompt
   of at most 1000 tokens.
4. THE PromptBuilder SHALL NOT include full skill markdown content in any per-fix prompt.
5. WHERE compact skill files are enabled, THE SkillFiles SHALL be at most 700 tokens for the
   builder skill, 800 tokens for the orchestrator skill, and 400 tokens for the transformer skill.

---

### Requirement 3: ExtendedFixups — New AST Rewrite Rules

**User Story:** As a migration engineer, I want the Java toolkit to handle additional known
Play→Spring patterns deterministically, so that fewer files require LLM intervention.

#### Acceptance Criteria

1. WHEN a Java file contains `javax.inject.Provider<T>`, THE ExtendedFixups SHALL rewrite it
   to `org.springframework.beans.factory.ObjectProvider<T>` and add the required import.
2. WHEN a Java file contains a call to `.get()` on an `ObjectProvider` field, THE ExtendedFixups
   SHALL rewrite the call to `.getObject()`.
3. WHEN a Java file contains `@Singleton` on a non-controller class, THE ExtendedFixups SHALL
   replace `@Singleton` with `@Component` and add `org.springframework.stereotype.Component`
   import.
4. WHEN any ExtendedFixups rewrite rule is applied to a Java file, THE ExtendedFixups SHALL
   produce the same output when applied a second time (idempotence).
5. WHEN any ExtendedFixups rewrite rule introduces a new type reference, THE ExtendedFixups
   SHALL add the corresponding fully-qualified import via `ensureImport()`.

---

### Requirement 4: Python CompileErrorFixer

**User Story:** As a migration engineer, I want a Python rule engine that applies deterministic
fixes to known compile errors, so that the LLM is only invoked for genuinely novel problems.

#### Acceptance Criteria

1. WHEN CompileErrorFixer.run() is called with a list of N errors, THE Fixer SHALL return a
   FixResult where `fixed_count + len(unresolved) == N`.
2. WHEN the Fixer applies any fix, THE Fixer SHALL write a `.bak` backup of the target file
   before modifying it.
3. WHEN the Fixer applies a fix that produces syntactically invalid Java (detected by a
   subsequent compile parse error), THE Fixer SHALL revert the file from its `.bak` backup
   and move the error to `unresolved`.
4. WHEN the Fixer processes any error, THE Fixer SHALL never modify files under the Play
   source repository.
5. WHEN the Fixer encounters error E01 (`cannot find symbol.*class ObjectMapper`), THE Fixer
   SHALL add `import com.fasterxml.jackson.databind.ObjectMapper;` to the affected file.
6. WHEN the Fixer encounters error E02 (`cannot find symbol.*class JsonNode`), THE Fixer
   SHALL add `import com.fasterxml.jackson.databind.JsonNode;` to the affected file.
7. WHEN the Fixer encounters error E03 (`package play\.\w+ does not exist`), THE Fixer
   SHALL remove the matching Play import line from the affected file.
8. WHEN the Fixer encounters error E10 (`method .* is already defined`), THE Fixer SHALL
   remove the duplicate method, retaining the Spring-idiomatic version.
9. WHEN the Fixer encounters error E11 (`cannot find symbol.*class RestTemplate`), THE Fixer
   SHALL generate a `@Bean RestTemplate` configuration class in the Spring repository.
10. WHEN the Fixer applies a fix, THE Fixer SHALL append a human-readable entry to
    `FixResult.det_fix_log` describing the change.

---

### Requirement 5: ErrorClusterer

**User Story:** As a migration engineer, I want compile errors grouped by root cause before
LLM dispatch, so that one LLM call can fix many files at once.

#### Acceptance Criteria

1. WHEN ErrorClusterer.cluster() is called with a list of N errors, THE Clusterer SHALL return
   clusters where the sum of all `cluster.count` values equals N.
2. WHEN ErrorClusterer.cluster() is called, THE Clusterer SHALL assign each error to exactly
   one cluster.
3. WHEN ErrorClusterer.cluster() is called, THE Clusterer SHALL return clusters sorted in
   descending order by `count`.
4. WHEN multiple errors share the same normalized message template (same symbol name, different
   files), THE Clusterer SHALL group them into a single cluster.
5. WHEN a cluster has a matching entry in the taxonomy, THE Clusterer SHALL populate
   `cluster.suggested_fix` with the taxonomy hint.

---

### Requirement 6: PromptBuilder

**User Story:** As a migration engineer, I want prompts constructed from minimal, structured
templates, so that cursor-agent receives only the information it needs.

#### Acceptance Criteria

1. THE PromptBuilder SHALL produce a system prompt containing the Spring repo path, Play repo
   path, and state file path.
2. WHEN PromptBuilder.fix_prompt() is called, THE PromptBuilder SHALL include the clustered
   error root cause, representative file and line, and error message for each cluster.
3. WHEN PromptBuilder.fix_prompt() is called, THE PromptBuilder SHALL instruct cursor-agent
   to edit only files under `spring_repo/src/main/java/` and not modify Play source files.
4. WHEN PromptBuilder.fix_prompt() is called with more than 5 clusters, THE PromptBuilder
   SHALL include at most 5 clusters in the prompt.
5. WHEN a cluster has a `suggested_fix`, THE PromptBuilder SHALL include it as a hint in the
   fix prompt.

---

### Requirement 7: IncrementalCompiler

**User Story:** As a migration operator, I want compilation scoped to changed modules, so that
compile cycles are faster on large multi-module projects.

#### Acceptance Criteria

1. WHEN IncrementalCompiler.compile() returns with `returncode == 0`, THE Compiler SHALL
   return an empty `errors` list.
2. IF IncrementalCompiler.compile() returns with `returncode != 0`, THEN THE Compiler SHALL
   return a non-empty `errors` list.
3. WHEN IncrementalCompiler.compile() is called, THE Compiler SHALL return a `duration_sec`
   value greater than zero.
4. WHEN IncrementalCompiler.compile() is called on a multi-module Maven project with
   `changed_files` provided, THE Compiler SHALL invoke `mvn compile -pl :<module> --am`
   targeting only the module containing the changed files.
5. WHEN IncrementalCompiler.compile() cannot determine the affected module, THE Compiler
   SHALL fall back to a full `mvn compile`.
6. WHEN IncrementalCompiler.compile() is called on a single-module project, THE Compiler
   SHALL invoke `mvn compile` without module targeting.

---

### Requirement 8: v1 → v2 Status File Migration

**User Story:** As a migration operator, I want existing migration-status.json files upgraded
transparently, so that in-progress migrations are not disrupted by the redesign.

#### Acceptance Criteria

1. WHEN migrate_v1_to_v2() is called on any status dict, THE Migrator SHALL set
   `schema_version` to `2` in the result.
2. WHEN migrate_v1_to_v2() is called, THE Migrator SHALL preserve the `status`,
   `files_migrated`, and `validate_iteration` values for every existing migration unit.
3. WHEN migrate_v1_to_v2() is called on a v1 status with `errors_history` arrays, THE Migrator
   SHALL convert each to `error_fingerprints` containing at most 5 entries of sorted string
   lists.
4. WHEN migrate_v1_to_v2() is called, THE Migrator SHALL add `det_fix_log: []` to each unit
   that does not already have it.
5. WHEN migrate_v1_to_v2() is called, THE Migrator SHALL add `prompt_cache_key: null` to the
   `autonomous` block if not already present.
6. WHEN migrate_v1_to_v2() is called on a dict that already has `schema_version: 2`, THE
   Migrator SHALL return the dict unchanged.

---

### Requirement 9: Orchestrator Loop Control and Resumability

**User Story:** As a migration operator, I want the orchestrator to detect stuck loops and
respect LLM budgets, so that migrations terminate predictably and can be resumed after
interruption.

#### Acceptance Criteria

1. WHEN the orchestrator detects that the current round's error fingerprints match a previous
   round's fingerprints for the same unit, THE Orchestrator SHALL escalate to an LLM call or,
   if `llm_calls >= escalate_after_retries`, mark the unit as `failed` with
   `failure_reason = "stuck"`.
2. WHEN `total_llm_calls` reaches `max_total_llm_calls`, THE Orchestrator SHALL mark the
   current unit as `failed` with `failure_reason = "budget_exhausted"` and exit with code 4.
3. WHEN the orchestrator exits with code 4, THE Orchestrator SHALL preserve the `status` of
   all previously completed units as `"done"`.
4. WHEN the orchestrator is re-invoked after a previous run, THE Orchestrator SHALL resume
   from the first unit whose `status` is not `"done"`, without re-processing completed units.
5. WHEN a migration unit transitions to `"done"`, THE Orchestrator SHALL write
   `migration-status.json` atomically before processing the next unit.
6. WHEN the orchestrator completes all units, THE Orchestrator SHALL run a full `mvn compile`
   cross-module validation pass before setting `current_step` to `"verify"`.

---

### Requirement 10: Error Handling and Safety

**User Story:** As a migration engineer, I want the pipeline to handle infrastructure failures
and bad fixes gracefully, so that partial progress is never lost.

#### Acceptance Criteria

1. WHEN a compile error message matches `"illegal start of expression"` or
   `"reached end of file"` in a file that was just edited by the Fixer, THE Fixer SHALL treat
   this as a bad-fix condition and revert the file from its `.bak` backup.
2. WHEN the Compiler detects an infrastructure error (JDK crash, Lombok annotation processor
   failure), THE Orchestrator SHALL exit immediately with code 5 without marking the unit as
   `failed`.
3. WHEN the Fixer completes a successful compile round, THE Fixer SHALL delete all `.bak`
   files created during that round.
4. IF a targeted `mvn compile -pl :<module>` passes but a subsequent full compile fails,
   THEN THE Orchestrator SHALL feed the full-compile errors back into the deterministic-first
   loop for the affected unit.
