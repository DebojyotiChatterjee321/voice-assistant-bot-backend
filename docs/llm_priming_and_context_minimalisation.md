# LLM Priming & Context Minimalisation

This note documents the latency optimisations introduced for Gemini priming and context footprint reduction. The reference implementation lives in [`02_LLMPrimingAndMinimalising.py`](../02_LLMPrimingAndMinimalising.py).

## Objectives

1. **Eliminate first-turn cold starts** by issuing an out-of-band request against Gemini before the user speaks.
2. **Keep the working context minimal** so subsequent turns send only the required dialogue history and tool metadata.

Both improvements run transparently inside the existing `run_bot` pipeline and require no client-side changes.

## Warm Path Priming

**Where:** `ensure_warm_paths()` (@server/02_LLMPrimingAndMinimalising.py#362-388)

* `ensure_warm_paths` is guarded by an `asyncio.Lock` so the warm-up runs once per process start.
* A short “warm-up ping” conversation is built with the production system prompt and queued into `GoogleLLMService.run_inference` before any real user turn.
* Failures log a warning but do not block the conversation; the pipeline proceeds even if priming fails.

**Why it matters:** Gemini caches the recent prompt state. Priming shifts TTFB and model all-in latency out of the first human turn, dramatically reducing perceived start-up lag.

## Context Minimalisation

**Where:** `LLMContextPruner` (@server/02_LLMPrimingAndMinimalising.py#79-148) inserted between the user context aggregator and the LLM in the pipeline (@server/02_LLMPrimingAndMinimalising.py#338-347).

Key behaviours:

1. **Turn count trimming** – keeps at most three recent user/assistant exchanges (six messages) while always preserving the system prompt.
2. **Message content truncation** – limits each message to 600 characters by default, ensuring verbose replies or transcripts cannot exceed the target payload.
3. **Structured payload pruning** – recursively trims nested Gemini `parts`, tool call arguments, and multimodal attachments without altering their structure.

These measures reduce prompt token usage from later turns, improving response time and cost while leaving the conversation semantics intact.

## Operational Notes

* The pruner runs on every downstream `LLMContextFrame`, so real-time conversation updates immediately benefit from trimming.
* Adjust `max_turns` or `max_chars` via `LLMContextPruner` constructor if different retention policies are needed. The defaults target sub-3K prompt tokens under typical customer flows.
* Priming relies on the same system prompt used for production turns; update both together to avoid divergence.

## File Reference

See [`02_LLMPrimingAndMinimalising.py`](../02_LLMPrimingAndMinimalising.py) for the complete, runnable example that integrates both strategies.
