# pipecat-dograh-piiguard

PII redaction for [Dograh](https://github.com/dograh-hq/dograh)'s Pipecat-based
voice pipeline. Detects sensitive entities in chat transcripts and audio call
history, replaces them with safe placeholders (or hashes / masks), and can
stop raw PII from ever reaching your LLM provider. Detection runs locally by
default — zero-dependency regex with Luhn / IBAN mod-97 / SSN validation, plus
context-anchored US ZIP detection and Polish national-ID patterns. Plug in
Microsoft Presidio for stronger NER on names and organizations when you need
it.

Built against Dograh's actual pipeline (a fork of Pipecat, `dograh-hq/pipecat`,
vendored as a git submodule) using Pipecat's `FrameProcessor` architecture.

## Why a separate distribution

This package has zero hard dependencies, deliberately:

- Pipecat's own top-level `pipecat` package is a real, fully-owned package
  (`pipecat.frames`, `pipecat.processors`, `pipecat.services`, ...) with no
  official third-party extension namespace. So this package does not attempt
  to live under the `pipecat` namespace — it's a flat top-level package,
  `pipecat_dograh_piiguard`, avoiding any collision with the real framework.
- Only `pipecat_dograh_piiguard.processor` needs the real `pipecat` package
  importable at runtime, and it's intentionally *not* listed as a
  dependency: Dograh's fork isn't published under a stable PyPI name (it's
  installed from a git submodule inside Dograh's own environment), so
  pinning a `pipecat` dependency here would either be wrong or redundant.
  Everything else in this package — `Redactor`, the detectors, the
  strategies — imports with zero dependencies at all.

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
   PII never reaches your LLM provider.

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
it's off the critical path.

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

## What gets detected (default regex backend)

| Group | Entities |
|---|---|
| `pii` | `EMAIL_ADDRESS`, `PHONE_NUMBER`, `US_SSN`, `US_ZIP_CODE`, `DATE_OF_BIRTH` |
| `pci` | `CREDIT_CARD` (Luhn-validated), `IBAN` (mod-97-validated) |
| `network` | `IP_ADDRESS`, `URL`, `MAC_ADDRESS` |
| `secrets` | `AWS_ACCESS_KEY`, `GITHUB_TOKEN`, `JWT` |
| Other | `BITCOIN_ADDRESS`, Polish national IDs (`PL_POSTAL_CODE`, `PL_PESEL`, `PL_NIP`) |

## Replacement strategies

| Strategy | Output | Use case |
|---|---|---|
| `placeholder` (default) | `[EMAIL_ADDRESS]` | Preserves conversational context |
| `hash` | `#EMAIL_ADDRESS_a1b2c3d4` | Deterministic — same input → same hash |
| `mask` | `**************1111` | Fixed-width replacement, `visible_tail=4` for card-last-4 |
| `redact` | (empty string) | Drop the entity entirely |

## Install

```bash
pip install pipecat-dograh-piiguard
# Optional Presidio backend for named-entity recognition
pip install "pipecat-dograh-piiguard[presidio]"
python -m spacy download en_core_web_lg
```

## Known gap: realtime speech-to-speech

Dograh's realtime pipeline (`build_realtime_pipeline`, for models like OpenAI
Realtime / Gemini Live handling audio natively) has no separate STT/TTS
stage — the realtime LLM handles audio-to-audio directly and only broadcasts
a `TranscriptionFrame` downstream for logging. `PIIRedactionProcessor` can
still scrub what gets *stored* on that path (insert it right after the
realtime LLM), but it cannot stop raw audio containing PII from reaching the
remote realtime provider — that data has already left as audio before any
frame reaches this processor. This is an audio-level redaction problem, out
of scope for this processor (it works on text frames only).

## What this plugin is and isn't

**Is:** a transcript-level PII redactor for Dograh's Pipecat pipeline.
Detects sensitive entities, swaps them for safe placeholders, and can
redact both in-flight (before the LLM sees it) and at the point transcripts
are persisted.

**Isn't:** an audio-level redactor (no beep-out in the recording itself), a
full HIPAA compliance product, or a substitute for retention controls. For
"zero data ever stored" use cases, configure your STT/LLM provider for
zero-retention and use this plugin on the still-in-process transcript before
logging.

## License

Apache-2.0.
