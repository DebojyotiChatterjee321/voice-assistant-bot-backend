# STT & VAD Tightening

Documentation for the speech-to-text and voice activity detection tuning introduced in [`03_TightenSTT_VAD_Behaviour.py`](../03_TightenSTT_VAD_Behaviour.py).

## Goals

1. **Sharper turn detection** – cut down on long hangups after the user stops speaking and avoid premature interruptions mid-utterance.
2. **Cleaner transcripts** – keep Deepgram output consistent (punctuation, formatting) while streaming interim results in near real time.
3. **Synchronised server/client VAD** – rely on both Deepgram's server-side `vad_events` and Silero's local detector for redundancy.

## Deepgram Live Options

Configured in `run_bot` when constructing `DeepgramSTTService` (@server/03_TightenSTT_VAD_Behaviour.py#287-299).

| Option | Value | Purpose |
| --- | --- | --- |
| `encoding` | `linear16` | Matches WebRTC PCM stream. |
| `language` | `"en"` | Forces English model. |
| `model` | `nova-3-general` | Latest low-latency model. |
| `channels` | `1` | Mono audio to reduce bandwidth. |
| `interim_results` | `True` | Enables fast partial transcripts. |
| `smart_format` | `True` | Adds punctuation & casing without post-processing. |
| `punctuate` | `True` | Explicit punctuation control. |
| `vad_events` | `True` | Emits start/stop speech events from Deepgram. |

The inline configuration ensures predictable behavior regardless of project-level defaults, and the language setting uses the string literal supported by the SDK.

## Silero VAD Parameters

Tightened values are set when constructing `SileroVADAnalyzer` inside the SmallWebRTC transport (@server/03_TightenSTT_VAD_Behaviour.py#430-438).

| Parameter | Value | Effect |
| --- | --- | --- |
| `confidence` | `0.75` | Requires higher model certainty before declaring speech. |
| `start_secs` | `0.12` | User speech is recognised after ~120 ms, keeping snappy wakeup. |
| `stop_secs` | `0.3` | Quiet exits happen within ~300 ms after silence. |
| `min_volume` | `0.55` | Filters out low-volume background noise. |

These parameters shorten end-of-turn detection while preventing false positives from ambient noise or breathing sounds.

## Operational Notes

* Deepgram's server-side VAD and Silero run simultaneously; whichever detects the boundary first helps clamp the turn in the aggregator.
* If the conversation feels too sensitive (clipping) or sluggish, adjust `start_secs` / `stop_secs` in tandem. Increasing both equally maintains relative timing while changing aggressiveness.
* For multilingual sessions, update `language` to a supported Deepgram locale and consider separate Silero models tuned to the right phonetics.

## Reference

See [`03_TightenSTT_VAD_Behaviour.py`](../03_TightenSTT_VAD_Behaviour.py) for the full runnable example incorporating these changes.
