# Usage Guide

What this plugin actually redacts, exactly where to wire it into Dograh, and
— just as important — everywhere in Dograh it does **not** reach. This guide
exists because the honest answer to "does this protect PII in Dograh?" is
"partially," and pretending otherwise would be worse than not shipping it.

## 1. Install

```bash
pip install pipecat-dograh-piiguard
# Optional: better name/org recall via NER, for the stored-transcript hook only
pip install "pipecat-dograh-piiguard[presidio]"
python -m spacy download en_core_web_lg
```

No dependency on `pipecat` itself is declared (see README for why) — it must
already be importable, which it is inside Dograh's own backend.

## 2. Where to wire it in Dograh (exact locations)

Both edits happen in Dograh's `api/services/pipecat/` — this plugin ships
the processor and the wrapper; wiring them into the pipeline is two small
edits to Dograh's own code (no fork needed, no upstream file rewritten
wholesale).

### 2a. In-flight redaction (`api/services/pipecat/pipeline_builder.py`)

`build_pipeline()` currently builds a fixed processor list. Add one optional
parameter and one conditional append, inserted **after `stt`, before
`voicemail_detector`/`user_context_aggregator`** — as early as possible, so
redaction happens before voicemail classification, LLM context assembly, or
anything else sees the raw text:

```python
def build_pipeline(
    transport, stt, audio_buffer, llm, tts,
    user_context_aggregator, assistant_context_aggregator,
    pipeline_engine_callback_processor, pipeline_metrics_aggregator,
    voicemail_detector=None,
    recording_router=None,
    pii_guard=None,               # <-- new parameter
):
    processors = [
        transport.input(),
        stt,
    ]
    if pii_guard is not None:     # <-- new line
        processors.append(pii_guard)
    if voicemail_detector:
        processors.append(voicemail_detector.detector())
    ...
```

### 2b. Construct and pass it in (`api/services/pipecat/run_pipeline.py`)

Mirror the existing `voicemail_detection` idiom (same file, ~line 981-1013):
a flag under `workflow.workflow_configurations`, checked before
`build_pipeline()` is called (~line 1054):

```python
from pipecat_dograh_piiguard.processor import PIIRedactionProcessor
from pipecat_dograh_piiguard.redactor import Redactor

pii_config = (workflow.workflow_configurations or {}).get("pii_redaction", {})
pii_guard = None
if pii_config.get("enabled", False) and not is_realtime:   # see §4 — realtime can't use this
    pii_guard = PIIRedactionProcessor(
        Redactor(strategy=pii_config.get("strategy", "placeholder"))
    )

pipeline = build_pipeline(
    transport, stt, audio_buffer, llm, tts,
    user_context_aggregator, assistant_context_aggregator,
    pipeline_engine_callback_processor, pipeline_metrics_aggregator,
    voicemail_detector=voicemail_detector,
    recording_router=recording_router,
    pii_guard=pii_guard,
)
```

`workflow_configurations` is a freeform JSON column with no strict schema on
the backend (`api/schemas/workflow_configurations.py` uses
`extra="allow"` — `voicemail_detection` itself isn't a typed field either,
it just rides through the same way). So a `pii_redaction` key works with
**zero backend schema changes**. It just won't be toggleable from Dograh's
visual workflow builder UI unless you also add frontend support (see §5).

### 2c. Stored-transcript redaction (`api/services/pipecat/run_pipeline.py`, ~line 1070)

```python
from pipecat_dograh_piiguard.transcript_hook import RedactingTranscriptCoordinator
from pipecat_dograh_piiguard.detectors.presidio import PresidioPIIDetector

transcript_log_coordinator = RedactingTranscriptCoordinator(
    TranscriptLogCoordinator(in_memory_logs_buffer),
    redactor=Redactor(detector=PresidioPIIDetector(score_threshold=0.6)),
)
transcript_log_coordinator.attach_turn_tracking_observer(task.turn_tracking_observer)  # unchanged
```

This single coordinator instance is shared by both the standard and realtime
pipelines (confirmed: `run_pipeline.py` constructs it once, unconditionally,
after the realtime/non-realtime branch), so this one edit covers persisted
transcripts for **both** pipeline modes.

## 3. Full coverage map

This is the part that matters most. Dograh has at least six distinct places
transcript/call text can end up, and this plugin's two hooks cover two of
them. Verified against the actual `dograh-hq/dograh` and `dograh-hq/pipecat`
source, not assumed.

| # | Sink | Covered? | Why |
|---|---|---|---|
| 1 | LLM context (what the model reads) | ✅ Yes | `PIIRedactionProcessor`, inserted before `user_context_aggregator` |
| 2 | Persisted transcript (DB `logs`, object-storage transcript file) | ✅ Yes | `RedactingTranscriptCoordinator` wraps `TranscriptLogCoordinator.record_user_transcript`/`record_assistant_transcript` |
| 3 | QA analysis (reads transcript text for post-call scoring) | ✅ Yes (indirectly) | QA reads `USER_TRANSCRIPTION`/`BOT_TEXT` from the same `realtime_feedback_events` buffer that #2 feeds, so it inherits the redaction |
| 4 | **Realtime / speech-to-speech pipeline's LLM input** | ❌ **Not possible** | `build_realtime_pipeline()` has no `stt` stage or `TranscriptionFrame` boundary before the LLM at all — audio goes straight to the realtime provider (OpenAI Realtime, Gemini Live). There is nothing to intercept. |
| 5 | **OTEL/Langfuse tracing spans** | ❌ No | Every STT service (`@traced_stt`) patches its own `push_frame` to emit the raw transcript as a span attribute (`pipecat/.../tracing/service_decorators.py`), **before** any `FrameProcessor` in the pipeline list — including this one — ever sees the frame. If Langfuse export is enabled, raw transcript leaves the process regardless of where `PIIRedactionProcessor` sits. |
| 6 | **Live WebSocket transcript stream to the frontend** | ❌ No | `RealtimeFeedbackObserver` is a pipecat *observer*, which watches every `push_frame` call pipeline-wide (including the STT service's own push) and sends transcript text over WebSocket immediately — independent of processor ordering. |
| 7 | **`gathered_context` → outbound webhooks** | ❌ No | Structured data extracted by the LLM (name, phone, etc.) is injected into webhook payload templates (`api/tasks/run_integrations.py`) via a completely separate code path from the STT/context-aggregator flow. |
| 8 | **Tool-call arguments/results** | ❌ No | Logged directly by `RealtimeFeedbackObserver` into the persisted events buffer, bypassing `TranscriptLogCoordinator` entirely. If a tool receives/returns PII (e.g. a CRM lookup), it's stored unredacted. |
| 9 | Sentry error reporting | ⚠️ Unconfirmed | No scrubber found, but no confirmed direct transcript-in-message call either — latent risk, not a verified leak. |

**Bottom line:** this plugin is a real, correctly-built solution for #1–#3 —
the same scope the sibling LiveKit-facing engine covers. It is not a
complete "no PII leaves Dograh" solution; #4–#8 are separate problems in
separate parts of Dograh's codebase that this package does not touch.

## 4. Realtime pipelines: turn it off, don't half-enable it

Because #4 above is architecturally impossible to close with a frame
processor, **explicitly disable `pii_redaction` when `is_realtime` is
true**, the same way `voicemail_detection` is force-disabled for realtime
today (`run_pipeline.py`, the `if is_realtime and voicemail_config.get(...)`
branch). Silently no-opping would be worse than an explicit warning, since a
user who enabled "PII redaction" reasonably assumes it's protecting the
realtime path too. The example in §2b already gates on `not is_realtime`;
also log a warning so it's visible in call logs, mirroring the voicemail
pattern exactly.

## 5. Making it configurable from the workflow builder UI (optional, not included)

`voicemail_detection` has a full UI: a typed TypeScript interface
(`ui/src/types/workflow-configurations.ts`), a settings dialog
(`VoicemailDetectionDialog.tsx`), and a wiring point in the workflow settings
page. `pii_redaction` has none of that yet — it's backend-only, settable via
direct API/DB writes. Adding UI parity is real frontend work (React/TypeScript,
outside this package) and wasn't built here; it's worth doing if you want
non-technical users toggling this per workflow.

One related fix worth doing in the same pass: `api/services/configuration/masking.py`'s
`mask_workflow_configurations` doesn't mask `voicemail_detection.api_key`
today, so it already returns in plaintext on every workflow GET. If your
`pii_redaction` config ever carries a secret (e.g. an external detection
API key), add masking for that key at the same time — same bug, don't repeat it.

## 6. Testing

```bash
cd pipecat-dograh-piiguard
pip install -e ".[dev]"
python -m pytest tests/ -v
```

15 tests cover the redactor/detectors core and the processor/transcript-hook
logic against faithful stand-ins for Pipecat's `FrameProcessor` interface
(the real `dograh-hq/pipecat` fork isn't pip-installable outside Dograh's own
build, so it can't be exercised directly here). Before trusting this in
production, run an actual call through a real Dograh dev instance with
`pii_redaction.enabled=true` and confirm:

- The LLM never receives raw PII in its context (check via provider-side
  logging or a debug breakpoint in `user_context_aggregator`).
- The persisted transcript (`transcript_url` artifact, `logs.realtime_feedback_events`)
  shows placeholders, not raw values.
- Turn-completion timing isn't affected (the processor does one `await`
  per turn for detection; regex is sub-millisecond, Presidio is not — keep
  Presidio out of the in-flight path as documented).

## 7. Troubleshooting

- **"PII still reaches the LLM"** — confirm `pii_guard` was actually passed
  into `build_pipeline()` and appears in the constructed `Pipeline`'s
  processor list before `user_context_aggregator`. A `None` from a
  misspelled `workflow_configurations` key (e.g. `"pii_redaciton"`) fails
  silently since the config dict is freeform — no schema will catch a typo.
- **"Redaction works for text calls but not this specific workflow"** — check
  whether that workflow uses the realtime pipeline (`is_realtime`); per §3/§4
  it structurally cannot be covered by the in-flight processor.
- **"Transcript still shows raw data somewhere"** — check which sink (§3
  table) you're looking at. The DB `logs`/transcript artifact should be
  clean; the live WebSocket feed, Langfuse traces, webhook `gathered_context`,
  and tool-call argument logs will not be, until those are separately addressed.
