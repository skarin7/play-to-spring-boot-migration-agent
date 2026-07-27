# LangGraph migration engine

`play-to-spring-kit/agent/` is the LangGraph-based rewrite of the Play →
Spring Boot migration orchestrator. It replaces the imperative
`scripts/migration_orchestrator.py` (the "legacy" engine, still available
via `--engine legacy`) with an explicit state graph: deterministic steps are
plain graph nodes, and LLM calls are bounded, path-jailed agent nodes that
never invoke each other directly — every LLM edit is verified by the next
deterministic node in the graph, never by the LLM's own self-report.

As of **M5**, `start_upgrade.sh` defaults to this engine
(`MIGRATION_ENGINE=langgraph`). This document is the map: what the graph
does, how to run it, how it resumes, and how to use `--interactive` mode.

## Why a rewrite

The legacy orchestrator is a single ~3000-line script: hardcoded phase
order, a `while True` compile-fix loop, hand-rolled resumability via a
`migration-status.json` file it reads and rewrites on every iteration, and
two stateless one-shot `cursor-agent` CLI calls for its only LLM use
(project bootstrap, and residual compile-fix rounds). Routes conversion,
Spring config-key mapping, and runtime wiring were manual gaps — humans did
them by hand, guided by skill markdown.

The LangGraph engine keeps the same guardrails (budget/retry/timeout/loop
detection) and the same JAR-based deterministic tooling
(`java-dev-toolkit`), but:

- Resumability is `SqliteSaver`-backed (checkpoint on every graph step),
  not a JSON file a human or process has to keep consistent.
- LLM calls are OpenRouter-backed (`langchain-openai`), not `cursor-agent`.
- Routes conversion, config-key mapping, and Spring Boot runtime
  verification are automated graph phases (M4), not manual follow-up work.
- Human-in-the-loop is a real `interrupt()`/resume primitive (M5), not "the
  human edits the status file by hand and reruns."

## Quick start

```bash
export OPENROUTER_API_KEY=sk-or-...   # required for LLM rounds; deterministic
                                       # fixers still run without it
./start_upgrade.sh --play-repo /path/to/your-play-app
```

`start_upgrade.sh` defaults to the langgraph engine now. To use the legacy
engine instead (during the transition, or to compare behavior):

```bash
./start_upgrade.sh --engine legacy --play-repo /path/to/your-play-app
# or: MIGRATION_ENGINE=legacy ./start_upgrade.sh --play-repo ...
```

Running the engine directly (bypassing `start_upgrade.sh`'s venv/JDK setup):

```bash
cd play-to-spring-kit
.venv/bin/python -m agent --spring-repo /path/to/spring-repo --play-repo /path/to/play-repo
```

## Architecture

### Package layout

```
play-to-spring-kit/agent/
  cli.py            entry point (python -m agent)
  config.py         AgentConfig — env-var-backed settings, guardrails
  llm.py            OpenRouter ChatOpenAI factory + bounded tool-call loop
  state.py           MigrationState TypedDict, outcome/exit-code tables
  checkpoint.py      SqliteSaver wiring, thread_id derivation
  status_v2.py       migration-status.json v2 dual-write (human-readable
                      artifact) + legacy-status adoption on resume
  graph.py           topology/wiring only (build_graph, RuntimeCtx, recursion_limit)
  nodes/             one module per phase — see below
  agents/            one module per LLM agent (bootstrap, compile_fix, routes,
                      config_mapping, runtime_wiring)
  tools/             deterministic subprocess/parsing wrappers (fs jail,
                      toolkit JAR, HOCON config, routes parsing, Maven boot)
```

`graph.py` only builds and wires the graph — every node/router function
lives in `agent/nodes/*.py`, one module per phase, each exposing either
plain top-level functions (routers that only read `state`) or a
`build(config, ctx) -> dict[str, Callable]` factory (nodes that need
`config`/`ctx`, following the `RuntimeCtx` closure convention below).

### Phases

| Phase | Milestone | Module | What it does |
|---|---|---|---|
| Bootstrap | M3 | `nodes/bootstrap.py` | Scaffolds `pom.xml`/`Application.java`/`application.properties` via a premium-tier LLM agent if they don't already exist; verified deterministically (file existence), never by LLM self-report. |
| Slice pipeline | M2 | `nodes/slice_pipeline.py` | Discovers migration units from the Play repo, runs `migrate-app` per unit via the JAR, then the compile-fix loop, then a plausibility check. |
| Compile-fix loop | M1 | `nodes/fix_loop.py` | `compile → det_fix → cluster → guard → agent → compile`. Deterministic fixers (pom dependency inference, known-pattern rewrites) run first; the LLM agent is only reached if they made no progress and the guard (budget/retries/timeout/loop-fingerprint) allows it. |
| Human gate | M5 | `nodes/human_gate.py` | Sits between a compile's infrastructure-error result and finalizing it. Headless (default): pass-through, same as always. `--interactive`: pauses via `interrupt()` so a human can retry or accept the failure. |
| Routes | M4 | `nodes/routes.py` | Parses the Play `conf/routes` file, diffs against Spring `@*Mapping` annotations already present, and has a cheap-tier LLM agent annotate the gaps (bounded attempts). Writes `.migration/route-map.json`. Non-blocking: always terminates, remaining gaps are recorded, never fails the run. |
| Config mapping | M4 | `nodes/config_mapping.py` | Maps well-known Play HOCON keys to Spring-idiomatic property names (a small seed table, e.g. `mongodb.uri` → `spring.data.mongodb.uri`) deterministically, then a cheap-tier LLM agent for the rest (bounded attempts). Writes `.migration/config-map.json`. Also non-blocking. |
| Boot verification | M4 | `nodes/boot.py` | Actually runs `mvn spring-boot:run` and watches for the Spring Boot startup line within a timeout. If it never starts, a tier-escalating LLM agent (cheap → premium) fixes runtime wiring (missing beans, bad config) and the graph loops back to another boot attempt, up to a budget. **This is the one phase where failure blocks the whole run** — every other M4 phase is best-effort. |

Routes → config-mapping → boot verification run once, after every slice is
processed — not per slice.

### The generic "fix cycle" mechanism

Routes-agent edits are Java changes and can break compilation. Rather than
duplicate the compile-fix loop, any phase that needs to make an edit and
then re-verify it re-enters the *same* `fix_loop` subgraph: it sets
`state["phase"] = "fix_cycle"` and `state["fix_cycle_return_to"]` to the
node it wants control back at once the cycle ends non-fatally, then edges
to `"compile"`. The shared `done`/`infra`/`halt` nodes exit to either
`slice_finalize` (normal slice pipeline) or the single generic
`after_fix_cycle` node (any fix-cycle re-entry) based on `state["phase"]`
alone — adding a new phase costs one line in `after_fix_cycle`'s outgoing
edge map, not a new node or router.

### Guardrails

Same accounting as the legacy orchestrator, enforced by `nodes/common.py`'s
`phase_budget_decision` (shared by routes/config_mapping/runtime_wiring) and
`guards.py` (the M1 compile-fix loop): global LLM call budget
(`MAX_TOTAL_LLM_CALLS`), per-slice retry cap, wall-clock timeout per slice,
and error-fingerprint loop detection. Budget exhaustion is always checked
*before* a phase's own local attempt cap, and always aborts the whole run
(`run_outcome="budget_exhausted"`, exit 4) — it's the one guardrail with
this precedence everywhere.

## CLI reference (`python -m agent`)

| Flag | Default | Notes |
|---|---|---|
| `--spring-repo PATH` | *(required)* | Target Spring Boot project. |
| `--play-repo PATH` | none | Source Play project. Omit to run standalone against an already-scaffolded Spring repo (treats it as one slice; skips routes/config-mapping, which need a Play repo). |
| `--slice-id ID` | `default` | Label for standalone/single-slice runs. |
| `--dry-run` | off | No subprocess/file-writing side effects; deterministic tools log what they would do. |
| `--fresh` | off | Ignore any existing checkpoint (new thread_id) **and** skip legacy `migration-status.json` adoption — a genuinely blank slate. |
| `--interactive` | off | Pause on infrastructure compile errors for a human decision instead of finalizing immediately (see below). |
| `--verbose`, `-v` | off | Debug-level logging. |
| `--workspace PATH` | parent of `--play-repo` | Passed to `setup.sh`. |
| `--spring-name NAME` | none | Passed to `setup.sh`. |
| `--toolkit-root PATH` | sibling `java-dev-toolkit/` | Override the dev-toolkit source location. |
| `--skip-build-toolkit` | off | Require an existing JAR under `play-to-spring-kit/lib/` instead of building it. |
| `--export-play-conf` | off | Flatten `conf/application.conf` into `application.properties` during setup (feeds the config-mapping phase). |
| `--conf-strip-prefix PREFIX` | `[]` (repeatable) | Extra HOCON prefixes to drop during conf export (e.g. `akka.` is always stripped). |
| `--max-bootstrap-attempts N` | `2` | Bootstrap agent retry cap. |

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `OPENROUTER_API_KEY` | *(none)* | Required for any LLM round. Without it, deterministic fixers still run; LLM-gated phases resolve to `no_llm`/skip gracefully rather than crashing. |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | Any OpenAI-compatible endpoint works. |
| `MIGRATION_MODEL_CHEAP` | `anthropic/claude-haiku-4.5` | First-tier model (compile-fix, routes, config-mapping, first runtime-wiring attempts). |
| `MIGRATION_MODEL_PREMIUM` | `anthropic/claude-sonnet-4.5` | Escalation tier (bootstrap; compile-fix and runtime-wiring after `ESCALATE_AFTER_RETRIES`). |
| `LLM_TIMEOUT_SEC` | `600` | Per-request timeout. |
| `MAX_TOTAL_LLM_CALLS` | `50` | Global run budget — the one guardrail that always aborts the whole run when exhausted. |
| `MAX_RETRIES_PER_LAYER` | `5` | Per-slice compile-fix retry cap. |
| `TIMEOUT_PER_LAYER_MINS` | `30` | Per-slice wall-clock timeout. |
| `ESCALATE_AFTER_RETRIES` | `2` | Retries before switching cheap → premium tier. |
| `MAX_AGENT_TOOL_CALLS` | `8` | Tool-call cap per LLM round. |
| `MAX_AGENT_CONTEXT_TOKENS` | `50000` | Context-budget threshold (input tokens) before `run_tool_loop` auto-compacts (headless) or asks once via `interrupt()` (`--interactive`). |

## Resumability

`SqliteSaver` at `<spring-repo>/.migration/langgraph-checkpoints.sqlite` is
the source of truth, keyed by `thread_id = sha1(spring_repo path)`. Rerun
the exact same command and the graph resumes from its last checkpoint — no
repeated JAR invocations or LLM calls for work already done.

`migration-status.json` (`<spring-repo>/migration-status.json`, repo root —
not under `.migration/`) is a **derived, human-readable artifact** written
after every run for people/tools reading it — never read back to resume a
run once a checkpoint exists.

### Adopting a legacy run

If you have a `migration-status.json` from a **legacy-engine** run (or from
a langgraph run whose checkpoint was lost) and no sqlite checkpoint exists
yet for this `spring_repo`, the langgraph engine adopts it automatically:
`migration_units` (with per-unit progress), `source_inventory`,
`migration_verification`, and the accumulated `total_llm_calls` count are
seeded into the initial state, so already-`done` units are skipped and the
run picks up where the legacy engine left off — including, for the first
time, running the M4 phases (routes/config-mapping/boot verification) that
the legacy engine never had. Pass `--fresh` to skip adoption and start
completely clean instead.

## `--interactive` / human-in-the-loop

Without `--interactive` (the default), an infrastructure compile error
(JDK/toolchain crash, not a normal Java error) finalizes the run
immediately with `outcome=infrastructure_error`, exit 5 — same as the
legacy engine.

With `--interactive`, the same failure instead pauses the run and prints
the failure's log tail, then prompts:

```
Compile hit an infrastructure error. Retry, or accept as a hard failure? [retry/abort]:
```

- `retry` loops back to `compile` (useful if it looked like a transient
  toolchain hiccup).
- anything else (including unrecognized input) finalizes as
  `infrastructure_error`, same as headless.

If the terminal session drops or you hit Ctrl-D/Ctrl-C at the prompt, the
CLI exits cleanly with code `130` and a message confirming the run is
**safely paused and resumable** — just rerun the exact same command. The
CLI detects a pending interrupt on startup and resumes it rather than
restarting the graph from scratch.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success. |
| 1 | Generic failure (setup failed, unhandled recursion-limit backstop). |
| 2 | No LLM available (`OPENROUTER_API_KEY` unset) and deterministic fixers got stuck. |
| 3 | Bootstrap init never completed after `--max-bootstrap-attempts`. |
| 4 | Global LLM call budget exhausted (`MAX_TOTAL_LLM_CALLS`) — the one outcome that always aborts immediately, at any phase. |
| 5 | A slice failed terminally, an infrastructure compile error occurred, or runtime wiring never got the app to start within its budget. |
| 6 | No migration units discovered. |
| 130 | (`--interactive` only) Paused awaiting a human decision — not a failure; rerun to resume. |

## What's still manual

- `docs/compile-fixes-for-toolkit.md` remains the living reference for
  recurring Play→Spring compile and runtime-wiring fixes — the
  `runtime_wiring` agent's prompt is distilled from its "Spring Boot
  runtime" section; update that doc, not the agent's prompt, when a new
  recurring pattern is found.
- A soak run against a real, non-trivial Play repository (beyond the test
  fixtures under `agent/tests/fixtures/`) has not been performed as part of
  this milestone — do that before fully retiring the legacy engine.
