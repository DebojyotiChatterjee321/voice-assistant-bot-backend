# Parallel Tool Access

Detailed notes for the Gemini tool parallelization introduced in [`05_ParallelizeToolAccess.py`](../05_ParallelizeToolAccess.py).

## Goal

Allow the LLM to dispatch multiple tool invocations concurrently so long-running lookups do not block each other, improving responsiveness for compound user queries.

## Implementation Highlights

* **Parallel flag** – `GoogleLLMService` is instantiated with `run_in_parallel=True`, turning on the Pipecat function runner’s parallel execution path. (@server/05_ParallelizeToolAccess.py#328-333)
* **Tool wrappers** – each SQLite-backed tool from `DatabaseTools` is wrapped by `_wrap_tool` before registration. The wrapper:
  1. Preserves the original signature via `functools.wraps` so schema generation still reflects the underlying tool. (@server/05_ParallelizeToolAccess.py#342-345)
  2. Catches exceptions so a failure in one concurrent task doesn’t crash the others, returning an error payload via `FunctionCallParams.result_callback`. (@server/05_ParallelizeToolAccess.py#346-357)
* **Tool registration** – wrapped callables are registered with `register_direct_function`, matching the LLM’s tool schema while enabling concurrency-safe execution. (@server/05_ParallelizeToolAccess.py#361-365)
* **Tool schema** – the conversation context advertises the wrapped functions, keeping alignment with what Pipecat expects to call. (@server/05_ParallelizeToolAccess.py#367-368)

## Operational Considerations

* Each tool call now runs in its own task. Ensure the backing database (SQLite) can handle simultaneous reads; current helpers open short-lived connections, which is safe for read-heavy workloads.
* If future tools perform writes or hold long locks, expand the wrapper to coordinate access or move to a pool.
* Errors return a generic retry message to the LLM; adjust messaging if you need finer-grained recovery flows.

Refer to the full implementation in [`05_ParallelizeToolAccess.py`](../05_ParallelizeToolAccess.py) for end-to-end context.
