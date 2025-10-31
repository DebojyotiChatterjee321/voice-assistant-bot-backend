# TimingObserver Queue Dwell Instrumentation

This document explains the queue dwell instrumentation added to `TimingObserver` in `server/01_TimingObserverInstrumentQueueDwellTime.py`. The goal of the change is to capture per-processor queue wait times so we can spot micro-bottlenecks in the Pipecat pipeline.

## Summary

- Each downstream `queue_frame` call records an enqueue timestamp on the frame metadata.
- A handler bound to `on_before_process_frame` reads the timestamp and computes the dwell time just before the processor handles the frame.
- Dwell times are aggregated per processor and per conversation turn.
- The per-turn log now includes average dwell times for each instrumented processor alongside the existing STT/Dialog/TTS durations.

## Implementation Details

### 1. Metadata Keys and Lifecycle State

`TimingObserver` introduces two metadata keys:

- `_timing_queue_enqueued`: stores a mapping of processor name → enqueue timestamp.
- `_timing_turn_id`: carries the turn identifier so dwell times can be grouped correctly.

Additional state tracks component ordering, dwell buckets, and which processors have already been instrumented.

### 2. Instrumenting Pipeline Processors

`TimingObserver.attach_pipeline(pipeline)` iterates over every processor in the pipeline assembled in `run_bot`. For each processor (except the observer itself) it:

1. Wraps `processor.queue_frame` so downstream frames capture the enqueue timestamp and turn id.
2. Registers an `on_before_process_frame` handler that measures dwell time and aggregates it under the associated turn.

The wrapper is bound using `MethodType` so the original queue behaviour is preserved while adding metadata bookkeeping.

### 3. Aggregating and Logging

- When a `TranscriptionFrame` arrives downstream, `TimingObserver` increments the turn id, initialises the dwell bucket, and records the STT timing (if provided by the frame metadata).
- Each dwell measurement is appended to a list keyed by processor name and turn id.
- When an `LLMFullResponseEndFrame` is observed, `TimingObserver` computes the average dwell per processor for that turn, formats a summary string, and appends it to the existing per-turn log line.

Example log output:

```
Turn 3: 'Your order ships tomorrow.' — STT: 182.4 ms | Dialogue: 642.1 ms | TTS: 319.8 ms | Total: 1144.3 ms | Queue dwell(ms): DeepgramSTTService: 5.3 | GoogleLLMService: 84.1 | ElevenLabsTTSService: 28.9
```

### 4. Usage

- Create the pipeline as usual in `run_bot`.
- Instantiate `TimingObserver` and include it as the final processor in the pipeline sequence.
- Immediately call `timing_observer.attach_pipeline(pipeline)` so every processor is instrumented before frames begin to flow.

No additional configuration is required at runtime; observers automatically record dwell time as long as `TimingObserver` is part of the pipeline.

### 5. File Reference

All implementation details are available in [`01_TimingObserverInstrumentQueueDwellTime.py`](../01_TimingObserverInstrumentQueueDwellTime.py). This file contains the complete, runnable example of the instrumented pipeline.
