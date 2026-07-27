# Heuristic Model Router — Design

## Problem

All 5 LLM agent call sites (`bootstrap`, `compile_fix`, `config_mapping`, `routes`,
`runtime_wiring`) pick a model with fixed, per-phase logic:

- `bootstrap.py:79` — always `config.model_premium`
- `config_mapping.py:70` — always `config.model_cheap`
- `routes.py:65` — always `config.model_cheap`
- `compile_fix.py:108` / `runtime_wiring.py:90` — `config.model_for_retry(retry_count)`,
  which escalates cheap → premium purely on retry count via `escalate_after_retries`.

None of this reacts to how much work a given round actually has to do. A `compile_fix`
round with 1 trivial error and a round with 20 cascading errors get the same model on
attempt 1. `bootstrap`/`config_mapping`/`routes` never escalate at all, regardless of
task size.

## Goal

Route each of the 5 agent calls to `model_cheap` or `model_premium` based on a cheap,
deterministic signal of task complexity — not just retry count — with zero added LLM
calls or latency.

## Non-goals

- No LLM-based meta-classifier (rejected: extra cost/latency on every call for
  marginal gain over heuristics).
- No third model tier (rejected: doubles config surface and test matrix for a v1;
  can be added later if two tiers prove insufficient).
- No change to `escalate_after_retries` semantics — retry-count escalation is
  preserved as one of two OR'd conditions.

## Architecture

`AgentConfig.model_for_retry(retry_count)` (`config.py:113`) is replaced by:

```python
@dataclass
class TaskSignals:
    retry_count: int = 0
    item_count: int = 0

def choose_model(self, signals: TaskSignals) -> str:
    if signals.retry_count >= self.escalate_after_retries:
        return self.model_premium
    if signals.item_count >= self.escalate_item_threshold:
        return self.model_premium
    return self.model_cheap
```

`TaskSignals` lives next to `AgentConfig` in `config.py`. `escalate_item_threshold`
is a new `AgentConfig` field: `_env_int("MIGRATION_ESCALATE_ITEM_THRESHOLD", 5)`,
following the existing `_env_int(name, default)` pattern.

`item_count` is a single generic "magnitude of remaining work this round" number —
each phase supplies its own meaning (see table below). One global threshold is used
across all phases rather than per-phase thresholds: the exact scales differ, but
more errors/unmapped-items/leftover-keys uniformly means "harder," so one knob is a
reasonable v1. Can be split into per-phase thresholds later if this proves too coarse
for one phase.

This is a pure function — no I/O, no LLM call — so it adds no new failure modes.
Absent/zero signals fall back to `model_cheap`, matching today's default behavior
for the phases that don't currently escalate.

## Per-phase signal wiring

Each call site already computes or has access to the values below; no new state
fields are introduced.

| Phase | `retry_count` source | `item_count` source |
|---|---|---|
| bootstrap | `attempt` (`nodes/bootstrap.py:68`, already computed, currently unused for model choice) | count of failing setup checks (`pom_ok`/`app_ok`/`props_ok` — 0..3) |
| compile_fix | `retry_count` (existing) | `len(clusters)` |
| config_mapping | `attempt` | `len(leftover)` |
| routes | `attempt` | `len(unmapped)` |
| runtime_wiring | `attempt - 1` (existing) | count of `Caused by:` occurrences in `boot_log_tail`, minimum 1 |

All 5 call sites replace their current `model_name = ...` line with:

```python
model_name = config.choose_model(TaskSignals(retry_count=..., item_count=...))
```

## Testing

- New `agent/tests/test_model_router.py`: unit tests for `choose_model` covering the
  4 cases — neither threshold crossed, retry-only escalation, item-only escalation,
  both.
- `agent/tests/test_runtime_wiring_agent.py` (currently references `model_for_retry`):
  update to call `choose_model` with equivalent `TaskSignals`.
- Each of the 5 agent test files gets one assertion that the `TaskSignals` values
  passed to `choose_model` match the phase's documented `item_count` source (e.g.
  `len(clusters)` for compile_fix).

## Error handling

None beyond the pure-function fallback above — `choose_model` cannot raise for any
input it's given (both fields default to `0`, comparisons are always valid ints).
