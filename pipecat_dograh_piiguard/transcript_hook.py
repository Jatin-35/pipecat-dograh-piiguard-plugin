"""End-of-call / stored-transcript redaction for Dograh.

Separate from :mod:`.processor`: this is about what ends up in your
database / logs buffer / realtime-feedback WebSocket, not what the LLM sees.
It's fine to run a heavier detector here (e.g. Presidio, for name/org recall)
since it's off the per-turn hot path.

Dograh's persisted transcripts flow through a single choke point:
``api/services/pipecat/transcript_log_coordinator.py``'s
``TranscriptLogCoordinator``, which has exactly two text-writing methods::

    record_user_transcript(*, text, timestamp, end_timestamp=None, event_timestamp=None)
    record_assistant_transcript(*, text, timestamp, end_timestamp=None, event_timestamp=None)

Rather than patching those methods (which would mean forking pipeline code),
wrap the coordinator instance with a redacting proxy before it's handed to
the rest of ``run_pipeline.py``::

    from api.services.pipecat.transcript_log_coordinator import TranscriptLogCoordinator
    from pipecat_dograh_piiguard.transcript_hook import RedactingTranscriptCoordinator
    from pipecat_dograh_piiguard.detectors.presidio import PresidioPIIDetector
    from pipecat_dograh_piiguard.redactor import Redactor

    transcript_log_coordinator = RedactingTranscriptCoordinator(
        TranscriptLogCoordinator(in_memory_logs_buffer),
        redactor=Redactor(detector=PresidioPIIDetector(score_threshold=0.6)),
    )
    transcript_log_coordinator.attach_turn_tracking_observer(...)  # unchanged

Everything else about turn lifecycle (speaking-started/ended, turn
started/ended) passes through untouched via ``__getattr__`` — only the two
text-writing calls are intercepted, so this needs zero changes to Dograh's
own pipeline/observer code.

Note this only covers the standard STT->LLM->TTS pipeline. Dograh's realtime
speech-to-speech pipeline still calls into the same coordinator for logging
(the realtime LLM broadcasts a ``TranscriptionFrame`` for that purpose), so
this hook does redact what gets stored there too — it just can't stop the
raw audio from having already reached the remote provider (see
``processor.py``'s docstring for that gap).
"""

from __future__ import annotations

from typing import Any

from .redactor import Redactor


class RedactingTranscriptCoordinator:
    """Transparent proxy around ``TranscriptLogCoordinator`` that redacts
    text on the way in, before it's appended to the persisted logs buffer.

    Duck-typed on purpose: takes any object exposing async
    ``record_user_transcript`` / ``record_assistant_transcript`` methods with
    a keyword-only ``text`` argument, so this module has no import-time
    dependency on Dograh's actual class.
    """

    def __init__(self, inner: Any, redactor: Redactor | None = None) -> None:
        self._inner = inner
        self._redactor = redactor or Redactor()

    async def record_user_transcript(self, *, text: str, **kwargs: Any) -> None:
        result = await self._redactor.redact(text)
        await self._inner.record_user_transcript(text=result.redacted, **kwargs)

    async def record_assistant_transcript(self, *, text: str, **kwargs: Any) -> None:
        result = await self._redactor.redact(text)
        await self._inner.record_assistant_transcript(text=result.redacted, **kwargs)

    def __getattr__(self, name: str) -> Any:
        # Everything else (attach_turn_tracking_observer, record_turn_started,
        # record_user_started_speaking, ...) passes straight through.
        return getattr(self._inner, name)
