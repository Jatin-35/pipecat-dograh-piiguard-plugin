# pipecat-dograh-piiguard

PII redaction for [Dograh](https://github.com/dograh-hq/dograh)'s Pipecat-based
voice pipeline. Same detection engine as
[`livekit-plugins-piiguard`](../README.md) — Luhn-validated credit cards,
mod-97-validated IBANs, context-anchored US ZIP detection, Polish national-ID
patterns, optional Presidio NER backend — rebuilt against Pipecat's
`FrameProcessor` architecture instead of LiveKit's `AgentSession`, since
Dograh does not use LiveKit Agents (its voice pipeline is a fork of Pipecat,
`dograh-hq/pipecat`, vendored as a git submodule).

## Why a separate distribution

This is a genuinely separate pip-installable package from
`livekit-plugins-piiguard`, not a namespace extension of it, for two reasons:

1. **No cross-framework dependency bloat.** `livekit-plugins-piiguard`
   depends on `livekit-agents`. If this plugin were folded into that same
   distribution, installing it for Dograh would drag in `livekit-agents` for
   users who have nothing to do with LiveKit. This package has zero hard
   dependencies.
2. **No namespace collision.** Pipecat's own top-level `pipecat` package is a
   real, fully-owned package (`pipecat.frames`, `pipecat.processors`,
   `pipecat.services`, ...) — unlike LiveKit, which deliberately ships
   `livekit/` as an empty namespace package specifically so plugins can
   extend it as `livekit.plugins.*`. There is no equivalent
   `pipecat.plugins.*` extension point, so this package does not attempt to
   live under the `pipecat` namespace; it's a flat top-level package,
   `pipecat_dograh_piiguard`.

The detection/redaction core (`types.py`, `strategies.py`, `detectors/`,
`redactor.py`) is duplicated from `livekit-plugins-piiguard` rather than
imported from it, for the same dependency-isolation reason. If a third
integration target ever shows up, factor the shared core into its own
package at that point.

## Two independent redaction points

Dograh's standard pipeline is `stt → user_context_aggregator → llm → tts`,
with transcripts also flowing to a separate persistence path
(`TranscriptLogCoordinator` → in-memory logs buffer → object storage / DB `logs`
column / realtime-feedback WebSocket). Those are two different things to
protect, so there are two independent hooks:

1. **In-flight** (`pipecat_dograh_piiguard/processor.py`) —
   `PIIRedactionProcessor`, a `FrameProcessor` inserted between `stt` and
   `user_context_aggregator` in `build_pipeline()`. Redacts
   `TranscriptionFrame.text` in place before it becomes LLM context, so raw
   PII never reaches your LLM provider. This is the equivalent of piiguard's
   in-flight mode (`on_user_turn_completed` override in the LiveKit plugin).

2. **Stored transcript** (`pipecat_dograh_piiguard/transcript_hook.py`) —
   `RedactingTranscriptCoordinator`, a transparent proxy around
   `TranscriptLogCoordinator` that redacts text in `record_user_transcript` /
   `record_assistant_transcript` before it's appended to the persisted logs
   buffer. Everything else (turn tracking, speech-start/stop timestamps)
   forwards through unchanged via `__getattr__` — zero forking of Dograh's
   pipeline code needed.

Use the fast regex detector (`RegexPIIDetector`, the default) for #1 since it
runs on the hot path every turn. Optionally use the heavier Presidio backend
(`PresidioPIIDetector`, better name/org recall, ~10-50ms/call) for #2 since
it's off the critical path — same trade-off the LiveKit plugin documents.

See [`examples/wiring_example.py`](examples/wiring_example.py) for the exact
edits against `api/services/pipecat/pipeline_builder.py` and
`api/services/pipecat/run_pipeline.py`.

## Usage

```python
from pipecat_dograh_piiguard.redactor import Redactor
from pipecat_dograh_piiguard.processor import PIIRedactionProcessor
from pipecat_dograh_piiguard.transcript_hook import RedactingTranscriptCoordinator

# In-flight: cheap regex detector on the hot path
pii_guard = PIIRedactionProcessor(Redactor(strategy="placeholder"))

# Stored transcript: heavier Presidio detector, off the hot path
from pipecat_dograh_piiguard.detectors.presidio import PresidioPIIDetector

transcript_log_coordinator = RedactingTranscriptCoordinator(
    TranscriptLogCoordinator(in_memory_logs_buffer),
    redactor=Redactor(detector=PresidioPIIDetector(score_threshold=0.6)),
)
```

## Install

```bash
pip install pipecat-dograh-piiguard
# Optional Presidio backend for named-entity recognition
pip install "pipecat-dograh-piiguard[presidio]"
python -m spacy download en_core_web_lg
```

`pipecat` itself is not a listed dependency: Dograh installs its own fork
(`dograh-hq/pipecat`) from a git submodule rather than from PyPI, so pinning
a `pipecat` dependency here would either be wrong or redundant. Only
`pipecat_dograh_piiguard.processor` needs `pipecat` importable at runtime —
it's there inside Dograh's own backend environment already. `Redactor` and
the detectors import with zero extra dependencies.

## Known gap: realtime speech-to-speech

Dograh's realtime pipeline (`build_realtime_pipeline`, for models like OpenAI
Realtime / Gemini Live handling audio natively) has no separate STT/TTS
stage — the realtime LLM handles audio-to-audio directly and only broadcasts
a `TranscriptionFrame` downstream for logging. `PIIRedactionProcessor` can
still scrub what gets *stored* on that path (insert it right after the
realtime LLM), but it cannot stop raw audio containing PII from reaching the
remote realtime provider — that data has already left as audio before any
frame reaches this processor. This is an audio-level redaction problem, out
of scope here just as it is for `livekit-plugins-piiguard`
("**Isn't:** an audio-level redactor").

## License

Apache-2.0, matching the sibling `livekit-plugins-piiguard` package.
