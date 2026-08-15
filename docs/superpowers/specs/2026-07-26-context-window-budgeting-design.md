# Context-window budgeting + auto-compact for `run_tool_loop`

## Problem

`run_tool_loop` (`play-to-spring-kit/agent/llm.py`) drives every LLM agent
(compile_fix, bootstrap, routes, config_mapping, runtime_wiring) through a
`while True` loop: model call → tool calls → model call → ..., capped only by
`MAX_AGENT_TOOL_CALLS` (a *count*, default 8). The full `messages` list is
resent on every iteration and nothing is ever trimmed or summarized. There is
no token-based guard analogous to Cursor/Claude Code stopping or compacting
near the context window — a handful of large `read_file` results (up to
40,000 chars each) stacking across iterations can grow the request far past
what's needed, with no backstop before a real context-length API error.

## Scope

Fix lives entirely in the shared `run_tool_loop` (`llm.py`) plus one new
`AgentConfig` field, so all five agents get it at once. No graph/state
changes, no new LangGraph nodes.

## Design

### New config (`config.py`)

```python
max_agent_context_tokens: int = _env_int("MAX_AGENT_CONTEXT_TOKENS", 50_000)
```

Same env-var-backed pattern as the existing guardrails
(`max_total_llm_calls`, `max_retries_per_layer`, etc.). Default 50,000 —
conservative headroom under Haiku 4.5's 200K context window (the smaller of
the two configured tiers; premium is Sonnet 4.5 at a much larger window),
leaving room to keep growing after a compact point before the next check.

### Trigger

Inside `run_tool_loop`'s loop, after each `bound.invoke(messages)`, the
response's real `usage.input_tokens` (already captured into
`result.input_tokens`) is used as the size proxy for what the *next* call
would send — one call lagged, but ground truth from the API, no separate
tokenizer or char-count estimate needed.

When that value exceeds `config.max_agent_context_tokens`, the loop is over
budget.

### Compaction (`compact_messages(messages, config) -> list`, new in `llm.py`)

- `messages[0]` (system) and `messages[1]` (the original task/user message)
  are never touched.
- The last 2 complete AI+Tool turns are kept verbatim as a tail. The cut
  point must land on a full turn boundary — an AI message's `tool_calls` and
  its matching `ToolMessage`s can never be split, since OpenAI-style APIs
  reject a request with an orphaned tool_call/tool_result pair.
- Everything between the task message and the kept tail is sent to one
  cheap-tier LLM call (`make_model(config, config.model_cheap)`) with a fixed
  instruction: summarize files read, edits made, errors seen so far, and the
  remaining plan.
- The result is spliced back in as a single synthetic
  `HumanMessage("[Earlier progress, compacted]\n" + summary)`, inserted
  between the task message and the kept tail.
- `ToolLoopResult` gains a `compactions: int` field (for `llm-usage.jsonl`
  visibility — how often compaction fired per round).

### Headless behavior (`config.headless == True`, the default)

Threshold crossed → auto-compact silently, log it, keep looping. No
behavioral change to any existing headless test path — matches
`human_gate.py`'s convention that headless mode never calls `interrupt()`.

### Interactive behavior (`--interactive`)

First threshold crossing in a given `run_tool_loop` invocation (one "round")
→ call `interrupt({"reason": "context_budget", "input_tokens": N,
"threshold": M})`. Decision:

- `"compact"` (also the default for empty/unrecognized input — same
  safe-default convention `human_gate.py` uses for "abort") — do the
  LLM-summary compaction described above.
- `"continue"` — skip compacting this round, proceed at the model's current
  size.

After this first ask, any further crossing within the *same* round
auto-compacts silently — no repeat prompts per round.

**Accepted tradeoff:** `interrupt()` pauses the whole graph node; on resume,
LangGraph re-executes the node function (and everything `run_tool_loop` did
inside it) from the top, replaying only previously-answered `interrupt()`
calls from cache — not the model calls or tool calls that ran before it. If
the process is resumed from a checkpoint after this interrupt fires, prior
model calls and file edits in that round replay for real: extra LLM cost,
and `str_replace` calls that already succeeded may see a harmless "old
string not found" error on replay (the edit was already applied). This is
narrow in practice — interactive-only, and only matters if the process
actually dies/restarts mid-round after the prompt fires — and is far less
scope than restructuring `run_tool_loop` into a dedicated graph node to avoid
it entirely (rejected as overkill for this corner case).

## Testing

- `compact_messages`: turn-boundary correctness (never splits a tool_call
  from its result), system/task message preservation, summary call uses the
  cheap-tier model.
- `run_tool_loop` with a fake model forcing `input_tokens` over threshold:
  verifies auto-compact fires in headless mode, `interrupt()` fires in
  interactive mode, and a second over-threshold crossing in the same round
  auto-compacts without a second prompt.

## Out of scope

- Per-model % thresholds (needs a context-window lookup table keyed to
  OpenRouter model strings) — noted as a future improvement, not blocking.
  Fixed token count is the agreed starting point.
- A dedicated graph node to avoid the interrupt-replay tradeoff — rejected,
  see above.
- Cheap/no-LLM-call truncation instead of LLM-summarized compaction —
  rejected; LLM summary was the explicit ask.
