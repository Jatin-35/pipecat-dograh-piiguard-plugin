# Backend Changes Required

Exact edits needed in `dograh-hq/dograh`'s `api/` to wire this plugin in, with
a per-workflow enable/disable toggle. Three files. Every edit below was
applied to a real clone of `dograh-hq/dograh`, verified with `python -m
py_compile` (both edited files compile clean), and cross-checked against the
actual runtime behavior of `TranscriptLogCoordinator.flush()`,
`register_turn_log_handlers()`, and the `WorkflowConfigurationDefaults`
Pydantic schema — not just written and assumed correct. See "Why these edits
are safe" at the bottom for what was specifically checked.

## 1. `api/requirements.txt`

Add the plugin as a dependency, pinned to a commit (it isn't published to
PyPI, same reasoning `noveum-trace` already uses in this file for the same
situation):

```diff
 tuner-pipecat-sdk==0.2.4
+# Pin to a reviewed commit, same reasoning as noveum-trace below (not on PyPI).
+pipecat-dograh-piiguard @ git+https://github.com/Jatin-35/pipecat-dograh-piiguard-plugin.git@0dfe90fe9cee6649aa66f4257c53272f5f677f52
 # Pin the reviewed wheel bytes, not only the package version.
 noveum-trace @ https://files.pythonhosted.org/packages/...
```

Update the commit hash to whatever's current on `main` when you actually do
this — `git ls-remote https://github.com/Jatin-35/pipecat-dograh-piiguard-plugin.git main`
gets you the latest.

## 2. `api/services/pipecat/pipeline_builder.py`

Add one parameter to `build_pipeline()`, and insert it into the processor
list immediately after `stt` — as early as possible, so redaction happens
before voicemail classification or LLM context assembly ever sees the raw
transcript:

```diff
 def build_pipeline(
     transport, stt, audio_buffer, llm, tts,
     user_context_aggregator, assistant_context_aggregator,
     pipeline_engine_callback_processor, pipeline_metrics_aggregator,
     voicemail_detector=None,
     recording_router=None,
+    pii_guard=None,
 ):
     """Build the main pipeline with all components.

     Args:
         ...
+        pii_guard: Optional PIIRedactionProcessor (pipecat_dograh_piiguard). When
+            provided, inserts immediately after STT so PII is redacted before
+            voicemail classification, LLM context assembly, or anything else
+            sees the raw transcript.
     """
     processors = [
         transport.input(),
         stt,
     ]

+    # Redact PII as early as possible - before voicemail classification and
+    # before user_context_aggregator turns the transcript into LLM context.
+    if pii_guard:
+        processors.append(pii_guard)
+
     if voicemail_detector:
         processors.append(voicemail_detector.detector())
     ...
```

`build_realtime_pipeline()` is **not** touched. It has no `stt` stage and no
text boundary before the LLM at all — audio goes straight to the realtime
provider (OpenAI Realtime, Gemini Live). There is nothing to insert a text
processor before. See §4 below for how the wiring explicitly accounts for this
rather than silently doing nothing.

## 3. `api/services/pipecat/run_pipeline.py`

Three parts: imports, constructing `pii_guard` + passing it to
`build_pipeline()`, and wrapping the transcript coordinator.

### 3a. Imports

```diff
 from api.services.pipecat.recording_router_processor import RecordingRouterProcessor
+from pipecat_dograh_piiguard.processor import PIIRedactionProcessor
+from pipecat_dograh_piiguard.redactor import Redactor
+from pipecat_dograh_piiguard.strategies import STRATEGIES as PII_REDACTION_STRATEGIES
+from pipecat_dograh_piiguard.transcript_hook import RedactingTranscriptCoordinator
 from api.services.pipecat.service_factory import (
```

### 3b. Construct `pii_guard`, gated by workflow config

Insert this right before the `# Build the pipeline` block (after
`recording_router` setup, before the `if is_realtime:` branch):

```python
# PII redaction: in-flight (pre-LLM) redaction is only possible on the
# non-realtime path, since build_realtime_pipeline has no stt /
# user_context_aggregator boundary to insert this at - the realtime LLM
# consumes audio directly. Mirrors the voicemail_detection realtime gate.
#
# workflow_configurations is freeform JSON with no schema validation
# (same as voicemail_detection), so a malformed pii_redaction value
# written directly via the API/DB - not just through the settings UI,
# which constrains the choices - must not crash call setup. Fall back to
# a safe default and log rather than let a config typo take down the call.
pii_config = (workflow.workflow_configurations or {}).get("pii_redaction", {})
if not isinstance(pii_config, dict):
    logger.warning(
        f"pii_redaction config for workflow run {workflow_run_id} is not an "
        f"object ({pii_config!r}); ignoring"
    )
    pii_config = {}
pii_strategy = pii_config.get("strategy", "placeholder")
if pii_strategy not in PII_REDACTION_STRATEGIES:
    logger.warning(
        f"Unknown pii_redaction strategy {pii_strategy!r} for workflow run "
        f"{workflow_run_id}; falling back to 'placeholder'"
    )
    pii_strategy = "placeholder"

pii_guard = None
if is_realtime and pii_config.get("enabled", False):
    logger.info(
        f"PII redaction is not available for realtime workflow run {workflow_run_id} "
        "(no text transcript exists before the model receives audio); "
        "only the stored transcript will be redacted"
    )
if pii_config.get("enabled", False) and not is_realtime:
    pii_guard = PIIRedactionProcessor(Redactor(strategy=pii_strategy))
```

Then pass it into the existing `build_pipeline(...)` call (non-realtime
branch only):

```diff
     else:
         pipeline = build_pipeline(
             transport, stt, audio_buffer, llm, tts,
             user_context_aggregator, assistant_context_aggregator,
             pipeline_engine_callback_processor, pipeline_metrics_aggregator,
             voicemail_detector=voicemail_detector,
             recording_router=recording_router,
+            pii_guard=pii_guard,
         )
```

### 3c. Wrap the transcript coordinator

Right after the existing `transcript_log_coordinator = TranscriptLogCoordinator(in_memory_logs_buffer)`
line, before `attach_turn_tracking_observer` is called:

```diff
     transcript_log_coordinator = TranscriptLogCoordinator(in_memory_logs_buffer)
+    if pii_config.get("enabled", False):
+        # Covers both pipeline modes: this single coordinator instance feeds
+        # persisted transcripts, QA analysis, and the transcript artifact for
+        # both realtime and non-realtime runs.
+        transcript_log_coordinator = RedactingTranscriptCoordinator(
+            transcript_log_coordinator,
+            redactor=Redactor(strategy=pii_strategy),
+        )
     if task.turn_tracking_observer is None:
         raise RuntimeError("Transcript logging requires turn tracking to be enabled")
     transcript_log_coordinator.attach_turn_tracking_observer(
         task.turn_tracking_observer
     )
```

This one wrapping covers **both** pipeline modes (realtime and non-realtime),
since `run_pipeline.py` constructs exactly one `TranscriptLogCoordinator`
instance regardless of which pipeline was built, and it's shared by
`register_turn_log_handlers()`, QA analysis, and the end-of-call transcript
artifact.

## 4. Enabling it — the config shape

No new backend schema is needed. `workflow_configurations` is validated by
`WorkflowConfigurationDefaults` with `extra="allow"` — `voicemail_detection`
itself isn't a typed field on that model either, it rides through the same
freeform path. Set this on a workflow (via the settings UI once you've also
applied `FRONTEND_CHANGES.md`, or directly via `PUT /api/v1/workflow/{id}` /
a DB write):

```json
{
  "pii_redaction": {
    "enabled": true,
    "strategy": "placeholder"
  }
}
```

`strategy` is one of `"placeholder"`, `"mask"`, `"hash"`, `"redact"`. Any
other value (or `enabled` left unset) safely falls back rather than erroring.

## Why these edits are safe

Specifically checked, not assumed:

- **`build_pipeline()`'s new `pii_guard` parameter is fully backward
  compatible** — it's optional, defaults to `None`, and appended after every
  existing parameter. No test in `api/tests/` calls `build_pipeline(` with a
  positional argument list that this would shift.
- **`RedactingTranscriptCoordinator.flush()` is not explicitly overridden**,
  but this is safe: it falls through `__getattr__` to the real
  `TranscriptLogCoordinator.flush()`, which only re-emits `side.text` —  a
  value that was set to the *already-redacted* text back when
  `record_user_transcript`/`record_assistant_transcript` first ran (the
  wrapper redacts before forwarding, so raw text never enters the
  coordinator's internal state at all). No redaction bypass at call-end flush.
- **A malformed `pii_redaction` config cannot crash call setup.** Both a
  non-dict value and an unknown `strategy` string are caught and logged
  rather than raising — verified by confirming `Redactor(strategy="typo")`
  raises `ValueError` at construction time, which is exactly what the guard
  now prevents from reaching the caller.
- **The realtime pipeline is explicitly gated off**, not silently
  no-op'd — a user who enables this on a realtime workflow gets an
  info-level log explaining exactly why in-flight redaction doesn't apply
  there (persisted-transcript redaction still does, via the shared
  coordinator).

## Known limitation carried over from the plugin itself

This covers the LLM context and the persisted/QA transcript. It does **not**
cover Dograh's live WebSocket transcript feed, OTEL/Langfuse tracing spans,
`gathered_context` sent to outbound webhooks, or tool-call argument/result
logging — those are separate code paths, untouched by this wiring. See the
plugin's own `GUIDE.md` for the full coverage map.
