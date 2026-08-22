"""How this plugs into Dograh's actual pipeline construction.

Not meant to run standalone — shows the exact edits against
``api/services/pipecat/pipeline_builder.py`` and ``api/services/pipecat/run_pipeline.py``
in the dograh-hq/dograh repo.
"""

from pipecat_dograh_piiguard.detectors.presidio import PresidioPIIDetector
from pipecat_dograh_piiguard.processor import PIIRedactionProcessor
from pipecat_dograh_piiguard.redactor import Redactor
from pipecat_dograh_piiguard.transcript_hook import RedactingTranscriptCoordinator

# ── 1. api/services/pipecat/pipeline_builder.py ────────────────────────────
#
# build_pipeline() currently does:
#
#     processors = [
#         transport.input(),
#         stt,
#     ]
#     ...
#     processors.append(user_context_aggregator)
#
# Add one optional processor between stt and user_context_aggregator/
# voicemail_detector, mirroring how voicemail_detector/recording_router are
# already threaded through as optional args:


def build_pipeline_patch(transport, stt, pii_guard=None, *rest):
    processors = [
        transport.input(),
        stt,
    ]
    if pii_guard is not None:
        processors.append(pii_guard)  # <-- new line
    # ... voicemail_detector, user_context_aggregator, llm, etc. unchanged
    return processors


# ── 2. api/services/pipecat/run_pipeline.py ─────────────────────────────────
#
# Construct the processor conditionally on a workflow_configurations flag,
# the same idiom used for voicemail_detection — including the same
# not-for-realtime gate voicemail_detection uses, since the realtime
# pipeline has no stt/user_context_aggregator boundary to insert this at
# (see GUIDE.md §3-4 for why):
#
#     pii_config = (workflow.workflow_configurations or {}).get("pii_redaction", {})
#     pii_guard = None
#     if pii_config.get("enabled", False) and not is_realtime:
#         pii_guard = PIIRedactionProcessor(Redactor(strategy=pii_config.get("strategy", "placeholder")))
#
#     pipeline = build_pipeline(
#         transport, stt, audio_buffer, llm, tts,
#         user_context_aggregator, assistant_context_aggregator,
#         pipeline_engine_callback_processor, pipeline_metrics_aggregator,
#         voicemail_detector=voicemail_detector,
#         recording_router=recording_router,
#         pii_guard=pii_guard,
#     )
#
# And currently:
#
#     transcript_log_coordinator = TranscriptLogCoordinator(in_memory_logs_buffer)
#
# Becomes:


def build_transcript_coordinator_patch(in_memory_logs_buffer, TranscriptLogCoordinator):
    return RedactingTranscriptCoordinator(
        TranscriptLogCoordinator(in_memory_logs_buffer),
        redactor=Redactor(detector=PresidioPIIDetector(score_threshold=0.6)),
    )


# Everything downstream of transcript_log_coordinator (turn tracking observer
# attachment, in-memory buffer reads for the UI, DB persistence) is untouched
# because RedactingTranscriptCoordinator forwards every other method via
# __getattr__ — only the two text-writing calls are intercepted.

__all__ = [
    "PIIRedactionProcessor",
    "build_pipeline_patch",
    "build_transcript_coordinator_patch",
]
