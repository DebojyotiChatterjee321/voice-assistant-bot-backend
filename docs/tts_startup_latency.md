# TTS Startup Latency Reduction

Documentation for the ElevenLabs warm path introduced in [`04_ReduceTTSStartupDelay.py`](../04_ReduceTTSStartupDelay.py).

## Objective

Reduce the time between first LLM response and audible speech by pre-paying the ElevenLabs connection cost before the user’s initial turn.

## Warm Path Sequence

Implemented inside `ensure_warm_paths()` (@server/04_ReduceTTSStartupDelay.py#395-423):

1. When the first client connects, acquire the warmup lock so multiple transports don’t duplicate the work.
2. Reuse the existing LLM priming call to avoid divergence between warm and live paths.
3. Call `warm_tts_connection()` to build a WebSocket context with ElevenLabs.
   - `run_tts(" ")` pushes a whitespace token, which forces the service to open a context and begin streaming.
   - The async generator is short-circuited after the first `None` sentinel to keep the pipeline free of audio frames.
   - `flush_audio()` cleans up any buffered silence.
   - Internal state (`_started`, `_context_id`) is reset so real responses start from a clean slate.

If either warm step fails, the system logs a warning but continues, keeping warmup best-effort and non-blocking.

## Operational Notes

* Warmup uses the same voice/model credentials as production playback, ensuring all caches are aligned.
* Because the ElevenLabs WebSocket stays active after warmup, subsequent turns avoid the handshake and first-byte delays (~500 ms improvement observed in testing).
* If you rotate voices or models dynamically, consider invalidating the warm cache (set `warm_done = False`) so the new combination is primed.

## Reference

For the full runnable example, see [`04_ReduceTTSStartupDelay.py`](../04_ReduceTTSStartupDelay.py).
