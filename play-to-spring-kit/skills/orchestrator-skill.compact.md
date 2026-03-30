---
name: play-spring-orchestrator-compact
description: Compact decision-tree skill for full Play→Spring migration orchestration.
---

# Orchestrator: Play → Spring Migration

## State: migration-status.json (Spring repo root)
Read on start. Write after each action. Resume from first non-done slice.

## Step 1: Initialize

IF initialize.status == done → SKIP
ELSE: Run builder skill Step 1 (pom.xml, Application.java, application.properties)
THEN: Set initialize.status = done, capture source_inventory

## Step 2: Transform + Validate (per slice)

### Unit Discovery
- DEFAULT (path units): Discover folders under Play app/ → migration_units array
  - Anchor: parent of controllers/ dir → one unit per immediate subdir
  - Fallback: ≥3 subdirs branching point
  - Last resort: whole app/ as single unit
- LEGACY (--use-semantic-layers): model → repository → manager → service → controller → other

### Per-Slice Loop
FOR each unit WHERE status ≠ done:
1. **Transform**: `java -jar dev-toolkit-1.0.0.jar migrate-app --path-prefix <prefix>`
   - Use --batch-size for large slices (>20 files)
   - CLI skips already-migrated files (idempotent)
2. **Validate** (deterministic-first):
   a. `mvn compile` (incremental if possible: -pl :module --am)
   b. IF infra error (JDK/Lombok crash) → exit code 5, do NOT mark failed
   c. IF dep errors → add Maven dependencies to pom.xml, re-compile
   d. IF code errors → run Python fixer (taxonomy E01–E12)
      - IF fixer resolved all → re-compile without LLM
      - IF unresolved remain → cluster errors, send to cursor-agent
   e. LLM escalation: use fix_model first, escalate_model after N retries
3. **Loop detection**: Compare error fingerprints to last 5 rounds
   - IF stuck + llm_calls ≥ escalate_after_retries → mark failed, reason=stuck
4. **Budget**: IF total_llm_calls ≥ max → mark failed, reason=budget_exhausted, exit 4
5. Mark unit done, write status atomically

## Step 3: Verify

AFTER all units done:
1. Full `mvn compile` (cross-module validation)
2. IF errors → feed back through deterministic fixer
3. Set current_step = verify → run verification → current_step = done

## Exit Codes
0=OK, 1=error, 2=stuck(no cursor), 3=init not done, 4=budget exhausted, 5=infra/fail-fast
