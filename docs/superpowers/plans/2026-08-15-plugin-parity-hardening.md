# Plugin Parity & Hardening Plan (M6)

**Status:** All 12 tasks implemented 2026-08-15/16. 427 tests passing (188
baseline + 239 new across both phases). Legacy engine untouched throughout.
**Date:** 2026-08-15
**Scope:** `play-to-spring-kit/agent/` (LangGraph engine only — legacy engine untouched)

## Phase 1 implementation notes

- **Task 1 (T2 signature diff):** `tools/signature_diff.py`,
  `nodes/signature_check.py`, wired into the graph between
  `slice_finalize`→`slice_router` (slice-scoped) and `verify`→`boot_run`
  (whole-tree final pass). Soft finding, never halts the run.
- **Task 2 (Play-repo guard):** `tools/play_guard.py` — git/manifest dual
  mode, baseline captured in `bootstrap.py:setup_node`, checked at the top of
  `fix_loop.py:compile_node` every round. **One real bug found and fixed
  during implementation:** `play_repo_guard_status` (and, generalized from
  it, every new state key added by this plan) must be declared in
  `state.py`'s `MigrationState` TypedDict — LangGraph's `StateGraph(MigrationState)`
  uses that TypedDict as its schema and **silently drops any node-returned
  key that isn't declared there**. Without the declaration, the guard logged
  "halting the run" but the run did not halt. This is not specific to this
  feature — it applies to any future state field.
- **Task 3 (search tool):** added to `tools/fs.py` alongside the two smaller
  fixes (str_replace occurrence line numbers, head+tail truncation).
- **Task 4 (prompt caching):** `_inline_affected_files` now precedes
  `fix_prompt` in `agents/compile_fix.py` (the ordering bug described below
  is fixed); `llm.py:run_tool_loop` marks system+user content with
  `cache_control` via `config.prompt_caching_enabled` (default on).
- **Task 5 (cost in dollars):** `pricing.py`, `config.max_total_cost_usd`
  (default 0/off), checked in `guards.decide` and
  `nodes/common.phase_budget_decision` at the same precedence as the
  call-count budget. `compact_messages`' previously-unmetered `model.invoke`
  now logs through `append_usage_log` under `phase="compact"`.

---

**Goal:** Close the gaps between the LangGraph engine and the `play-to-springboot`
Claude plugin (v1.2.1), across six axes: cost, context management, sub-agent
context handoff, observability, permissions/sandboxing, and tool use.

**Source of the gap list:** a side-by-side review of `agent/` against the plugin's
`docs/STATE-CONTRACT.md`, `docs/ORCHESTRATION.md`, `docs/PERMISSIONS.md`, and
`docs/GAPS.md`. Where a mechanism already exists in the plugin, this plan names it
so the design can be lifted rather than reinvented.

**Where the engine already leads the plugin** (keep as-is, do not regress):
context compaction (`llm.py:compact_messages` — the plugin has none), sqlite
resumability (`checkpoint.py`), `FsJail` as a code-level invariant rather than a
settings-file hook, and LLM-self-diagnosed `flag_for_manual_review` in preference
to a deterministic unmappable-package guess.

---

## The verification-tier gap (cuts across everything)

The plugin runs five verification tiers. The engine runs one.

| Tier | Plugin | Engine today |
|---|---|---|
| T1 compile | `gate.py` + `parse_mvn.py` | ✅ `nodes/fix_loop.py:compile_node` |
| T2 signature preservation | `signature_diff.py`, `method-missing` is a blocker | ❌ none |
| T3 route parity | `routes.py` / `verify.py` | ⚠️ routes agent edits; no parity assertion |
| T4 tests | `mvn test` at final gate | ❌ none |
| T5 endpoint parity | `endpoint_diff.py`, both apps booted, QA judges | ⚠️ `boot_run` proves it starts, nothing more |

`nodes/slice_pipeline.py:verify_node` runs a final compile plus
`inventory.run_verification` (file counts only). **Nothing checks that a public
method survived the migration.** A migration that compiles, boots, and silently
dropped half its methods passes every gate the engine has. T2 is the cheapest
missing tier — `java-dev-toolkit` already ships the `signature` subcommand.

---

## Ordering

Tasks are ordered by (value ÷ cost), not by axis. Each is independently
shippable; nothing below depends on a task after it.

| # | Task | Axis | Size |
|---|---|---|---|
| 1 | T2 signature-preservation tier | verification | M |
| 2 | Play-repo integrity guard | permissions | M |
| 3 | `grep` / search tool | tool use | S |
| 4 | Prompt caching | cost | S |
| 5 | Cost in dollars + token budget | cost | S |
| 6 | Architect phase + `decisions.md` | handoff | L |
| 7 | Per-file journal | handoff | M |
| 8 | `gaps.jsonl` learning loop | observability | M |
| 9 | Per-phase tool scoping + caps | tool use | S |
| 10 | T4 tests + T5 endpoint parity | verification | L |
| 11 | Run report | observability | M |
| 12 | Context-management follow-ups | context | M |

---

## Task 1 — T2 signature-preservation tier

**Why first:** the JAR support already exists, it catches silent data loss, it is
fully deterministic, and it needs no LLM. Highest value per line of code in this
plan.

**Files:**
- New: `agent/tools/signature_diff.py`
- New: `agent/nodes/signature_check.py`
- Modify: `agent/graph.py` (node + edge), `agent/state.py` (state fields)
- Test: `agent/tests/test_signature_diff.py`, `agent/tests/test_graph_signature_check.py`

**JAR contract — verified empirically 2026-08-15, not assumed.** Open question Q1
is resolved; see "Verified JAR contract" below for the evidence and the two
gotchas it turned up.

**Design:**

- `tools/signature_diff.py` wraps `java -jar <jar> signature <root> -o <out>` for
  both the Play source root and the Spring source root, following the existing
  `tools/toolkit_inventory.py` shape exactly: an injectable `run_cmd`, JSON report
  written to a temp path, `None` on dry-run or unparseable output.
  **Note the flag is `-o/--output`, not `--report`** — `inventory` uses
  `--source`/`--report`, `signature` takes a bare positional input plus `-o`. The
  two subcommands do not share a flag convention; copying `toolkit_inventory.py`'s
  argv shape verbatim will fail with `Unknown options: '--report'`.
- A pure `diff_signatures(play_json, spring_json, layer=None)` returns three
  buckets, mirroring the plugin's `signature_diff.py` semantics:
  - `method_missing` — a Play class present in Spring, but a public method gone. **Blocker.**
  - `classes_absent_from_spring` — class not migrated *yet*. **Not a finding** —
    this is what makes gating a partially-migrated slice safe by construction.
  - `signature_changed` — same method, different arity/return. **Major.**
- `nodes/signature_check.py` runs after `slice_finalize` for the current slice's
  `path_prefix`, scoped to that slice only. Records results into new state fields.
- Wire into `verify_node` as well, unscoped, for the whole-tree final pass.

**State additions** (`state.py:MigrationState`):

```python
signature_findings: list[dict[str, Any]]   # accumulates across the run
signature_check: dict[str, Any] | None     # last run's raw buckets
```

**Decision — a T2 blocker does not halt the run.** It records a finding, marks the
unit's `failure_reason`, and lets `slice_router` continue. Matches the plugin's
post-1.2.0 soft-failure model: only integrity violations halt (see Task 2).

**Tests:** classes-absent-is-not-a-finding (the partial-migration case);
method-missing is a blocker; arity change is major; missing JAR / dry-run returns
`None` without raising; slice-scoped run ignores other layers' classes;
`parse_error` entries are excluded rather than treated as zero-method classes.

### Verified JAR contract

Checked on 2026-08-15 by downloading the pinned release, verifying its checksum,
running it against a fixture tree, and diffing that against a build of current
`java-dev-toolkit/` source.

| Check | Result |
|---|---|
| Pinned release `toolkit-v1.0.1` downloads, sha256 matches `toolkit-release.json` | ✅ `9a58b89e…4cec1e` |
| Pinned JAR exposes `signature` | ✅ (also `inventory`) |
| Current source build vs pinned JAR, same fixture | ✅ **byte-identical output** |
| Plugin v1.2.1 pins the same release | ✅ same url + sha |
| Plugin's `signature_diff.py` consumes this exact shape | ✅ `files`/`class`/`methods`/`visibility`/`statements`/`parse_error` |

**Gotcha 1 — `1.0.0` jars on this machine have no `signature` command at all.**
Four `dev-toolkit-1.0.0.jar` copies exist under `~/Work` (in sample Play repos and
older kit checkouts). `1.0.0` ships neither `signature` nor `inventory`. Anything
resolving a JAR by glob rather than by the pinned path will silently pick one up
and fail with a picocli usage error, not a missing-file error. `fetch_jar.py`'s
checksum-pinned path is the only correct resolver.

**Gotcha 2 — flag convention differs per subcommand** (see Design above).

Actual output shape, from the pinned JAR:

```json
{
  "root": "t",
  "files": {
    "Sample.java": {
      "path": "Sample.java",
      "class": "Sample",
      "methods": [
        {"name": "add", "arity": 2, "visibility": "public",
         "returns": "primitive", "statements": 4}
      ],
      "fields": ["name"]
    },
    "sub/Broken.java": {
      "path": "sub/Broken.java",
      "parse_error": "Parse error. Found \"this\", expected one of …"
    }
  }
}
```

Contract details that matter for `diff_signatures`:

- **Keyed by path relative to the scanned root**, so the Play and Spring trees line
  up only if both are scanned from their respective source roots (`app/` vs
  `src/main/java/`). Passing repo roots instead produces two disjoint key sets and
  a diff that reports everything as missing.
- `returns` is a **coarse bucket** — `void|primitive|reference` — deliberately, so
  that `play.mvc.Result` → `ResponseEntity` is not flagged. Do not tighten this.
- `visibility` includes `package`; T2's blocker rule applies to `public` only.
- `statements` counts nested statements, so a method whose logic sits in one big
  `if` is not scored as 1. Useful as the hollowed-out-body signal the extractor
  was written for.
- **A `parse_error` entry has no `methods` key.** It must be excluded from the
  comparison, not read as a class with zero methods — otherwise an unparseable
  file reads as every method deleted. The plugin treats these as a separate
  `parse_errors` bucket and dispatches QA (a file that will not parse is
  unexamined, not passing); the engine should record them as findings for the
  report rather than as T2 blockers.
- Only a **fully successful** parse yields a signature — a partial parse is
  reported as `parse_error`, by design, so half-read files never masquerade as
  vanished methods.

---

## Task 2 — Play-repo integrity guard

**Why:** the read-only-Play invariant is currently enforced only for LLM writes,
by `FsJail._resolve_write`. Everything else escapes it: `setup_ops.run_setup_sh`
runs `bash setup.sh <play_repo>`, and `toolkit_jar` runs `migrate-app` with
`cwd=play_repo`. Nothing ever verifies the Play repo stayed untouched.

The plugin has three layers here (`docs/PERMISSIONS.md`): a PreToolUse hook that
denies (prevention), a generated `deny` permission list, and `guard.py` that
detects at every gate. The engine has none of the three. The engine cannot use a
Claude Code hook, so it needs the detection layer — the equivalent of `guard.py`.

**Files:**
- New: `agent/tools/play_guard.py`
- Modify: `agent/nodes/bootstrap.py` (baseline capture), `agent/nodes/fix_loop.py`
  (check before compile), `agent/graph.py`, `agent/state.py`
- Test: `agent/tests/test_play_guard.py`

**Design — lift the plugin's `guard.py` semantics, including its bug fixes:**

- Two modes. **git mode** only when the Play repo is its own repository root
  (`git -C <play> rev-parse --show-toplevel` equals the play repo path).
  **Manifest mode** otherwise — a sha256 manifest captured at bootstrap and
  stored at `<spring>/.migration/play-baseline.json`.
- Manifest mode is not a fallback for convenience; it is strictly better in two
  ways the plugin documents: it catches gitignored files, and it does not create a
  nested git repo inside somebody's checkout.
- Returns an explicit `"clean" | "tampered" | "error"`. **No code path where an
  empty result means clean** — this is the specific bug the plugin fixed: a Play
  repo that is not a git repository makes `git` exit 128 with empty stdout, which
  read as clean forever.
- **A missing baseline is `error`, not `clean`.** `error` halts exactly like
  `tampered`: a guard that could not run is not a guard that passed.

**Where it runs:** baseline captured once in `bootstrap`, checked at the top of
`compile_node` (every slice, every fix round — it is a file-stat walk, cheap
relative to `mvn`).

**This is one of only two conditions that halt the whole run** (the other being
the agent reporting it cannot proceed without editing Play). New run outcome:

```python
"play_repo_tampered": EXIT_INFRASTRUCTURE   # RUN_OUTCOME_EXIT_CODES
```

**Also in this task, smaller sandboxing fixes:**

- `tools/toolkit_jar.run_cmd` passes `timeout=None` explicitly. Give it a real
  timeout from a new `config.subprocess_timeout_sec` (default 1800).
- `FsJail.write_file` / `str_replace` ignore `config.dry_run` and write for real.
  Thread `dry_run` into `FsJail` and make writes no-ops that still record the
  intended edit.
- `build_toolkit_from_source` bypasses the checksum verification that
  `fetch_jar.py` performs. Log this loudly at the point of use; it is a
  deliberate developer escape hatch, not a default path.

**Tests:** non-git Play repo returns `error` not `clean` when baseline missing;
git mode detects a dirty worktree; manifest mode detects a gitignored-file edit;
`error` and `tampered` both halt; baseline capture is idempotent across resume.

---

## Task 3 — `grep` / search tool

**Why:** the highest-leverage single tool addition. Agents get `read_file`,
`write_file`, `str_replace`, `list_dir`, `flag_for_manual_review` — **no search**.
An agent resolving an unknown symbol can only `list_dir` + `read_file` its way
toward it, burning tool calls against the cap of 8 *and* burning context against
the 50k threshold on files it did not need. One search tool cuts both at once.

**Files:**
- Modify: `agent/tools/fs.py`
- Test: `agent/tests/test_fs_jail.py`

**Design:**

```python
@tool
def search(pattern: str, path: str = ".", max_results: int = 50) -> str:
    """Regex-search files under a directory. Returns path:line:text, capped."""
```

- Goes through `jail._resolve_read`, so it inherits the existing jail — searchable
  roots are exactly spring + play, no new surface.
- Pure Python `re` over a `rglob("*.java")`-style walk. No `subprocess`, no
  dependency on `rg`/`grep` being installed.
- Cap results *and* cap total output chars; return a `[truncated, N more matches]`
  marker so the model knows to narrow rather than assuming it saw everything.
- Skip `target/`, `.git/`, `.migration/`.

**Also in this task, two small tool-quality fixes:**

- `str_replace` returns `"old string occurs N times; must be unique"` with no
  further help, so the model guesses at wider context blindly. Return the line
  numbers of each occurrence.
- `MAX_READ_CHARS = 40_000` truncates mid-token at a hard offset. Change to
  head+tail with an elision marker — the end of a Java file (imports are at the
  top, the failing method is often at the bottom) is frequently the half that
  matters.

---

## Task 4 — Prompt caching

**Why:** likely the single largest cost win available. `status_v2.py` already
writes `"prompt_cache_key": None` — the field exists and is unused. Every
compile-fix round re-sends the full system prompt plus `_inline_affected_files`
contents at zero cache hit rate.

**Files:**
- Modify: `agent/llm.py` (`make_model`, `run_tool_loop`), `agent/config.py`
- Test: `agent/tests/test_prompt_cache.py` (new)

**Design:**

- Mark the system message and the stable prefix of the task message as cacheable
  via Anthropic's `cache_control` breakpoints, passed through OpenRouter.
- The cacheable prefix is the part that does not change between rounds of one
  slice: `PromptBuilder.system_prompt()`, plus the inlined file contents. The
  volatile suffix is the error clusters.
- **Ordering constraint:** for this to work, `_inline_affected_files` output must
  move *before* the cluster text in the user message. Today
  `agents/compile_fix.py` does `fix_prompt(clusters) + _inline_affected_files(...)`
  — that ordering puts the volatile part first and defeats caching entirely.
  Reverse it.
- Gate on `config.prompt_caching_enabled` (env `MIGRATION_PROMPT_CACHING`,
  default on), since not every OpenRouter-routed model supports it.
- Record `cache_read_input_tokens` / `cache_creation_input_tokens` from
  `usage_metadata` into `ToolLoopResult` and the usage log, so the win is
  measurable rather than assumed.

**Related, same task:** `_inline_affected_files` pushes file contents into the
prompt *and* the agent also has `read_file` — the same bytes can arrive twice.
With `search` (Task 3) available, reduce the inlined content to the error's
immediate neighbourhood and let the agent pull the rest.

---

## Task 5 — Cost in dollars, and a token budget

**Why:** tokens are logged but never priced. `MAX_TOTAL_LLM_CALLS=50` counts
*requests* — a 50-call budget can be $0.40 or $40 depending on whether
`choose_model` escalated to premium and how large the compacted transcripts grew.
**Call count is the wrong unit for a spend cap.**

**Files:**
- New: `agent/pricing.py`
- Modify: `agent/llm.py`, `agent/config.py`, `agent/guards.py`,
  `agent/nodes/common.py`, `agent/status_v2.py`
- Test: `agent/tests/test_pricing.py`, additions to `agent/tests/test_guards.py`

**Design:**

- `pricing.py` holds a per-model rate table (input / output / cache-read per
  million tokens), keyed by the OpenRouter model id, with an
  `MIGRATION_PRICING_JSON` env override so a rate change does not require a code
  change. Unknown model → `None` cost, logged once, never an exception.
- `append_usage_log` gains `input_cost_usd`, `output_cost_usd`, `total_cost_usd`.
- New `config.max_total_cost_usd` (env `MAX_TOTAL_COST_USD`, default `0` = off).
  When set, it is checked **alongside** `max_total_llm_calls` in both
  `guards.decide` and `nodes/common.phase_budget_decision`, and resolves to the
  same `"budget_exhausted"` decision and `EXIT_BUDGET_EXHAUSTED`. Follow the
  existing precedence rule exactly: global budget before any phase-local cap,
  aborting the whole run.
- Running total lives in state as `total_cost_usd: float`, surfaced in
  `status_v2` under `autonomous` next to `total_llm_calls`.

**Bug to fix in the same task:** `compact_messages` calls `model.invoke` directly,
bypassing the budget entirely — an unmetered LLM side channel that costs real
money and appears in no log. Route it through the usage log and count it against
both budgets.

---

# Phase 2 — implemented 2026-08-15/16

Tasks 6-12 below were scoped out of the first implementation pass because
each is a subsystem in its own right; they were then implemented in full in
a second pass, in the same order, bringing the total to 427 passing tests
(188 baseline + 75 Phase 1 + 164 Phase 2).

## Phase 2 implementation notes

- **Task 6 (architect phase):** `agents/architect.py` + `nodes/architect.py`,
  wired between `inventory` and `slice_router`. Writes `.migration/decisions.md`;
  `FsJail`'s write-jail widened to include `.migration/**` (not just
  `src/**`+`pom.xml`) so the architect agent can write it directly via
  `write_file`. `DECISIONS_READ_FIRST_LINE` appended to the other four agents'
  system prompts (push the path, never the contents — pull discipline).
  Interactive mode gates approval via `interrupt()`; headless auto-approves.
  **Six existing test files needed a `.migration/decisions.md` seed** added to
  their bootstrap helpers once this became a required phase for any
  `play_repo`-configured run — a real, bounded backward-compat cost of adding
  a phase in the middle of the pipeline.
- **Task 7 (per-file journal):** `tools/journal.py`, NDJSON per unit under
  `.migration/journal/`, folded via `journal_offsets` in `transform_node`.
  `toolkit_jar.migrate_until_done` gained an `on_batch` callback (opt-in,
  existing 2-arg test fixtures unaffected) so `_default_jar_runner` in
  `graph.py` can write one journal entry per `migrate-app` batch — the actual
  crash-recovery seam, since a multi-batch `migrate_until_done` call can run
  for a while before the containing graph node returns and the checkpoint
  advances.
- **Task 8 (gaps.jsonl):** `tools/gaps.py` (closed `GAP_KINDS` set, same
  append-only NDJSON discipline as Task 7) + `record_gap` tool added to
  `tools/fs.py`, plus `flag_for_manual_review` extended to also write a
  `agent_improvised` gap so a stop is never a silent loss of the diagnosis.
  Deterministic nodes record gaps too: `tool_error` when `migrate-app` itself
  reports errors, `boot_failure` when `runtime_wiring` exhausts its attempts.
  `scripts/gap_report.py render` — framework-symbol passthrough, sha256+salt
  redaction for everything else, salt persisted at
  `~/.config/play-to-spring-migration-agent/install-salt` (Q4 resolved: no
  network I/O anywhere in the module, verified by a test that patches
  `socket.socket` to raise).
- **Task 9 (tool scoping):** `FsJail.build_tools(phase=...)` returns a
  capability-scoped subset per phase (`_PHASE_TOOL_NAMES` table in `tools/fs.py`);
  `config.max_agent_tool_calls_for(phase)` adds a per-phase env override
  (`MAX_AGENT_TOOL_CALLS_<PHASE>`). All 6 agent call sites updated. Zero test
  regressions on the scoping change itself — confirms no existing test
  depended on a tool a phase no longer gets.
- **Task 10 (T4/T5):** `tools/maven.py:run_mvn_test` (T4, parses surefire's
  aggregate summary line, gated behind `config.run_tests`, skipped entirely
  when the final compile is still red). `tools/endpoint_diff.py` (T5) — pure,
  deterministic `build_probes`/`capture`/`diff_responses`; GET-only by
  default, mutating verbs and unresolved path params recorded as `unproved`
  rather than silently skipped; volatile fields (id/timestamp/duration/etc.)
  compared for presence+type only. **Two real regex bugs caught by tests**:
  a `$`-anchored volatile-field pattern silently never matched `durationMs`
  (anchor required exact suffix), and a naive `id$` check false-positived on
  `grid`/`valid` (fixed with a case-sensitive camelCase/snake_case-aware
  pattern). Dual-boot Play+Spring orchestration (Q2) is a documented pluggable
  seam (`ctx.endpoint_parity_runner`, no default implementation) rather than
  forced — same shape as `ctx.signature_runner` from Task 1, which solved the
  identical "subprocess this codebase has no wrapper for" problem.
- **Task 11 (report):** `report.py` — `console_summary` (blocker-only +
  cost + pointer to the file), `render_html`, `write_report`, `--report-only`
  CLI flag that regenerates from `migration-status.json` alone.
  `state["findings"]` (tier/severity/category/scope) added, populated by T4
  and T5; `status_v2.py` extended to carry findings/cost/test_result/
  endpoint_verification through so `--report-only` doesn't need a live graph
  run.
- **Task 12 (context follow-ups):** pre-flight token estimate
  (`_estimate_tokens`, chars÷4, no tokenizer dependency) now runs before
  every `bound.invoke`, not only detected from `usage_metadata` afterward.
  Compaction's raw middle-turn span is now written to
  `.migration/compacted/<id>.txt` and referenced by path in the synthetic
  summary message — retrievable via the same `read_file` tool agents already
  have, closing the "compacted content gone forever" gap. Tracing wired as
  run-level tags/metadata in `cli.py` (`config.tracing_enabled`, reads
  `LANGCHAIN_TRACING_V2`) plus per-round tags/`run_name` in
  `llm.py:run_tool_loop`, gated so an untraced run builds nothing extra.
  `legacy_logic.stuck_vs_progress_reason` classifies the same comparison
  `is_looping` makes (identical/oscillating/spike vs. progressing/different-
  error-set) for logging and the report's migration-units table, without
  changing `is_looping`'s own decision in either of its two call sites.

---

## Task 6 — Architect phase and `decisions.md`

**Why:** the engine goes `setup → bootstrap → inventory → transform` with no
research or architecture step. Every idiom call is improvised per-round inside
compile-fix, with no record and no reuse. The plugin calls its Gate 1 "the
cheapest correction point in the run"; the engine has no such point.

This also supplies the **cross-phase memory** that is otherwise missing entirely:
each `run_tool_loop` starts a fresh `messages` list, so the routes agent knows
nothing of what compile-fix learned. `decisions.md` is a durable artifact every
later agent reads off disk.

**Files:**
- New: `agent/agents/architect.py`, `agent/nodes/architect.py`
- Modify: `agent/graph.py`, `agent/state.py`, `agent/agents/*.py` (prompt inputs)
- Test: `agent/tests/test_architect_agent.py`, `agent/tests/test_graph_architect.py`

**Design:**

- Runs once, after `inventory` (which already produces
  `play_surface_inventory` with KNOWN/UNKNOWN/PARADIGM classification and
  `coveragePercent` — exactly the input an architect phase needs) and before the
  first `transform`.
- Writes `<spring>/.migration/decisions.md`: dependency map, config map, idiom
  decisions (notably the async policy — the plugin flags this as the one that
  repeats in every layer if wrong), the `no_migration` list, and open concerns.
- Every later agent prompt gains a short "read `.migration/decisions.md` before
  editing" line and the path — **the path, not the contents**. This is the
  plugin's push/pull rule: push paths and IDs, let the agent pull off disk.
- `no_migration` feeds Task 1's signature diff as a subtraction, so Play-only glue
  does not show as a permanent T2 shortfall on every run. A check that always
  complains is a check nobody reads.
- Interactive mode gets an `interrupt()` gate here to approve or revise —
  headless never interrupts, per the engine's existing invariant. **Headless
  auto-approves and logs that it did.**

**Explicitly not in scope:** a separate researcher role. The existing
`inventory` + `play_surface_inventory` already covers the survey the plugin's
researcher produces.

---

## Task 7 — Per-file journal

**Why:** state advances only at node boundaries in the sqlite checkpoint. A crash
mid-`transform` — a `migrate-app` batch of N files — loses that node's entire
progress. Checkpointing at node granularity cannot fix this; it needs a
finer-grained append-only record.

**Files:**
- New: `agent/tools/journal.py`
- Modify: `agent/nodes/slice_pipeline.py`, `agent/nodes/fix_loop.py`, `agent/state.py`
- Test: `agent/tests/test_journal.py`

**Design — lift the plugin's NDJSON discipline and its two hard-won details:**

- Append-only NDJSON at `<spring>/.migration/journal/<unit-id>.ndjson`. Appending
  has no read-modify-write step to be interrupted halfway; a truncated final line
  is skipped and every completed line before it is still good. This bounded damage
  is the entire reason for NDJSON over one JSON array.
- **`journal_offsets`** (`dict[str, int]` in state) records how many lines of each
  journal have been folded, so folding is idempotent — the plugin hit a bug where
  a re-dispatched three-file layer reported nine files migrated. Two edges to
  handle: a truncated final line leaves the offset short of itself (the next
  append completes that line); and a journal shorter than its recorded offset is
  replayed from the top, since it cannot be the file that offset was measured
  against.
- **Torn-line salvage.** Start each append with a newline, so a torn line stays
  isolated. Because that relies on the writer behaving, the *reader* must not
  depend on it: recover a trailing entry from a welded line, accepting only a
  suffix that both parses and carries an `action` key — so a nested object inside
  one well-formed entry is never mistaken for a second entry. Without salvage the
  counters drift *low*, and a lost `migrated` line reads as a unit with work still
  left.

Line shape:

```json
{"unit":"service","action":"migrated","count":3,"remaining":85}
{"unit":"service","action":"failed","file":"ContentService.java"}
{"unit":"service","action":"compiled","error_count":7}
```

---

## Task 8 — `gaps.jsonl` learning loop

**Why:** `flag_for_manual_review` is a good *stop signal* but not a *recorded,
aggregatable data point*. The plugin's insight is that the improvisation itself is
the data — `what_i_did` is the load-bearing field. "Hand-ported to `@Async`" is
worth a release; "could not find a mapping" is worth nothing. And it must be
recorded **even when the run succeeds** — a gap that produced a green run is the
one nobody will ever find by looking at failures.

**Files:**
- New: `agent/tools/gaps.py`, `agent/scripts/gap_report.py`
- Modify: `agent/tools/fs.py` (extend `flag_for_manual_review`, add `record_gap`)
- Test: `agent/tests/test_gaps.py`

**Design:**

- Append-only `<spring>/.migration/gaps.jsonl`, same discipline as Task 7.
- A new `record_gap(kind, subject, what_i_did, layer)` tool available to every
  agent — it does **not** stop the loop, unlike `flag_for_manual_review`. Extend
  `flag_for_manual_review` to also write a gap line so a stop is never a silent
  loss of the diagnosis.
- `kind` is a **closed set**, not free text — free text cannot be counted, which
  is the whole point of collecting it: `unmapped_dependency`, `unhandled_idiom`,
  `tier_blind_spot`, `tool_error`, `agent_improvised`, `layout_surprise`,
  `boot_failure`.
- Deterministic nodes record gaps too: a `tool_error` on JAR failure, a
  `boot_failure` when `runtime_wiring` exhausts its attempts.
- `gap_report.py render` writes `.migration/gap-report.md`. **Framework symbols
  pass through; everything else is hashed** with a per-install salt. `play.*`,
  `org.springframework.*`, `akka.*` are public API and are what make a gap
  actionable; `com.acme.Pricing` is the user's business and becomes
  `<class:a1b2c3d4>`. Same salt across one install means "hit this four times" is
  visible; different salt across installs means the same class name at two
  companies never collides.
- **No network code in that tool at all.** No automatic upload, no automatic rule
  promotion. The report is a file the user reads and decides about.

---

## Task 9 — Per-phase tool scoping and caps

**Why:** all five phases get the same five tools and the same
`max_agent_tool_calls=8`. The routes agent gets `write_file`; the config-mapping
agent gets `list_dir`. Their needs differ by an order of magnitude. The plugin's
role-based tool grants (dev gets `Edit`/`Write`, the others do not) is the pattern
to copy.

**Files:**
- Modify: `agent/tools/fs.py` (`FsJail.build_tools(phase=...)`), `agent/config.py`,
  all five `agent/agents/*.py`
- Test: `agent/tests/test_fs_jail.py`

**Design:**

- `build_tools(phase: str)` returns a phase-appropriate subset. Read-mostly phases
  do not receive `write_file` at all — capability removal beats prompt instruction.
- Per-phase caps via `config.max_agent_tool_calls_for(phase)`, defaulting to the
  current global value so nothing changes unless explicitly tuned. Env override per
  phase: `MAX_AGENT_TOOL_CALLS_COMPILE_FIX`, etc.
- Steer edits toward `str_replace` over whole-file `write_file` — the latter is
  both the expensive and the lossy path. Prompt-level first; consider dropping
  `write_file` from compile-fix once Task 3's better `str_replace` diagnostics
  make failures self-correcting.

---

## Task 10 — T4 tests and T5 endpoint parity

**Why:** compile, signatures, and routes prove the code builds, kept its methods,
and answers at the right paths. **None of them prove it returns the same thing.**

**Files:**
- New: `agent/tools/endpoint_diff.py`, `agent/nodes/endpoint_parity.py`
- Modify: `agent/nodes/slice_pipeline.py` (`verify_node` → T4), `agent/graph.py`,
  `agent/state.py`
- Test: `agent/tests/test_endpoint_diff.py`, `agent/tests/test_graph_endpoint_parity.py`

**Design:**

- **T4:** `mvn test` in `verify_node`, gated behind `config.run_tests` (env
  `MIGRATION_RUN_TESTS`, default on). Failures are findings, not a halt.
- **T5:** reuse the existing `route_map` from `tools/routes_parser.py` to seed
  probes. Boot Play, capture; boot Spring (`tools/maven.py` already wraps a
  long-lived `Popen` for exactly this), capture; diff.
- **Parameterless GET routes enabled by default; POST/PUT/PATCH/DELETE disabled.**
  Mutating verbs need two things `conf/routes` does not record: a request body, and
  identical starting state in both apps, since the first capture changes what the
  second reads. Where that cannot be arranged, **a GET-only comparison is the
  honest check and the mutating paths are recorded as unproved — not as a pass.**
- Timestamps, ids, and durations are compared for **presence and type, not
  equality** — two runs of the same app differ there. Field ordering is never a
  difference.
- Anything left over is a finding. The plugin dispatches a QA agent to judge these;
  the engine's equivalent is a bounded LLM round over the diff, with the diff
  itself (deterministic) as the evidence.

---

## Task 11 — Run report

**Why:** the engine's terminal artifact is a JSON blob and an exit code. The
plugin renders `report.html` plus a chat summary filtered to blocker severity —
everything else lives in the report, not the console.

**Files:**
- New: `agent/report.py`
- Modify: `agent/cli.py`, `agent/graph.py` (`run_done_node`, `run_halt_node`)
- Test: `agent/tests/test_report.py`

**Design:**

- Renders `<spring>/.migration/report.html` from `migration-status.json` plus the
  new artifacts: signature findings (Task 1), cost totals (Task 5), gaps (Task 8),
  endpoint diff (Task 10).
- Console summary prints **blocker-severity findings only**, plus the run outcome
  and total cost. Everything else is a pointer to the report.
- A `--report-only` CLI flag regenerates it without re-running anything or touching
  migration state.

**Findings need a severity model first.** The engine has per-slice `outcome` enums
but no finding-level granularity. Add, alongside the state fields from Tasks 1
and 10:

```python
findings: list[dict[str, Any]]   # id, tier, severity, layer, file, evidence, status
```

with `severity: blocker|major|minor` and `status: open|fixed|accepted`, matching
the plugin's `qa_findings`. **This is also the feedback channel** — attaching
finding IDs and evidence to the next fix round is what makes the loop
self-correcting rather than a blind retry.

---

## Task 12 — Context-management follow-ups

The engine's compaction is ahead of the plugin's; these are refinements, not
repairs.

**Files:** `agent/llm.py`, `agent/config.py`, `agent/tests/test_context_budget.py`

- **Compaction triggers after the overflow was already paid for.** The check is
  `current_input_tokens > threshold`, evaluated on the response — meaning the
  oversized request was already built, sent, and billed. Add a pre-flight estimate
  before `bound.invoke` (character-count heuristic is sufficient; no tokenizer
  dependency) and compact *before* sending.
- **Compaction is lossy with no reload path.** Middle turns are replaced by a
  summary and dropped from `messages` forever. The raw record is in
  `llm-debug.jsonl` but nothing ever reads it back. Either write compacted spans to
  a retrievable artifact, or give the agent a tool to re-read one — the plugin's
  answer to the same problem is artifacts on disk plus a pull.
- **Add tracing.** `request_id` correlates one tool loop; there is no run-level
  trace id and no spans. LangGraph makes LangSmith/OTel export near-free — wire it
  behind an env flag.
- **Surface stuck-vs-progress.** `legacy_logic.is_looping` runs on fingerprints
  internally, but nothing reports which case fired. The plugin's observation is
  that *a different, even larger error set usually means a fix landed and exposed
  errors beneath it* — the exact case a fingerprint heuristic can misjudge. Log the
  comparison and put it in the report.

---

## Constraints that apply to every task

- **Headless never calls `interrupt()`.** Every test compiles the graph without a
  checkpointer and this is what makes that possible. Any new interactive gate must
  preserve the headless-never-interrupts branch.
- **Verification stays deterministic and separate from the LLM.** An agent's edits
  are checked by the next graph node, never by asking the model whether it
  succeeded. Tasks 1 and 10 add tiers; none of them may be self-reported.
- **New tunables follow the `config.py` pattern**: `_env_int(name, default)` plus a
  dataclass `field(default_factory=...)`. No ad-hoc `os.environ` reads elsewhere.
- **Global budget precedence is invariant.** `MAX_TOTAL_LLM_CALLS` (and now
  `MAX_TOTAL_COST_USD`) is checked before any phase-local cap and always aborts the
  whole run with `EXIT_BUDGET_EXHAUSTED`. Task 5 extends this rule; it must not
  weaken it.
- **`nodes/` stays split.** `graph.py` passed ~1000 lines before it was broken out.
  New phases get new `nodes/` modules; `graph.py` does not grow again.
- **Legacy engine is not touched.** `scripts/legacy/migration_orchestrator.py` stays
  as-is.

## Open questions

1. ~~**Does the shipped JAR's `signature` subcommand emit the shape Task 1
   assumes?**~~ **RESOLVED 2026-08-15.** Verified by running the pinned release,
   not by reading docs. Pinned `toolkit-v1.0.1` checksum matches, exposes
   `signature`, and its output is byte-identical to a build of current source; the
   plugin pins the same release and its `signature_diff.py` consumes exactly this
   shape. Task 1 is unblocked. Two gotchas found and folded into Task 1: `1.0.0`
   jars lying around this machine have **no `signature` command at all**, and
   `signature` uses `-o` where `inventory` uses `--report`. See "Verified JAR
   contract" under Task 1.
2. **[Task 10] T5 needs both apps bootable simultaneously**, on different ports,
   with separate datastores. **SIDESTEPPED, not resolved, 2026-08-16.** Rather
   than force a dual-boot implementation with unverified feasibility, T5's
   deterministic diff engine (`tools/endpoint_diff.py` — probes, capture, diff)
   was built and fully tested; the actual orchestration is a pluggable seam
   (`RuntimeCtx.endpoint_parity_runner`, no default implementation, degrades to
   `endpoint_verification.status == "not_attempted"`) — same shape as
   `signature_runner` (Task 1), which solved the identical "subprocess this
   codebase has no wrapper for" problem. Writing the actual Play-boot wrapper
   (repo-dependent: sbt run vs. a fat jar vs. something else) is follow-up work,
   not blocked on anything in this plan.
3. **[Phase 1, Task 4] Prompt-caching support varies by OpenRouter-routed model.**
   Implemented behind `config.prompt_caching_enabled` (default on) so a model that
   rejects `cache_control` degrades to uncached rather than erroring — see Task 4.
4. **[Task 8] The salt for gaps-hashing needs a stable per-install home.**
   **RESOLVED 2026-08-16.** `~/.config/play-to-spring-migration-agent/install-salt`
   (or `$XDG_CONFIG_HOME/...`), generated once on first use by
   `scripts/gap_report.py:get_or_create_salt`. Chosen over a per-spring-repo file
   under `.migration/` specifically so the salt persists across repos on one
   machine — matching the plugin's stated property that the same salt across an
   install's runs makes "hit this four times" visible, while a different salt
   per machine keeps two installs' hashes from colliding.
