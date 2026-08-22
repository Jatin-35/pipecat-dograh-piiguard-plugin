"""In-flight PII redaction for Dograh's Pipecat pipeline.

Insertion point (see ``api/services/pipecat/pipeline_builder.py:build_pipeline``)::

    processors = [
        transport.input(),
        stt,
        PIIRedactionProcessor(redactor),   # <-- insert here
        ...
        user_context_aggregator,           # builds LLM context from TranscriptionFrame
        ...
    ]

Placed after ``stt`` and before ``user_context_aggregator``, this guarantees
redacted text is what actually reaches the LLM — the Pipecat equivalent of
``livekit-plugins-piiguard``'s in-flight mode (its ``on_user_turn_completed``
override), rather than piiguard's default end-of-call-only mode.

Only touches ``TranscriptionFrame`` / ``InterimTranscriptionFrame`` (user
speech). ``LLMTextFrame`` / ``TTSTextFrame`` (bot output) are intentionally
left alone here — redact those with a second instance placed between ``llm``
and ``tts`` if you also want to stop the bot from repeating PII it read out
of a tool result.

Known gap: Dograh's realtime speech-to-speech pipeline
(``build_realtime_pipeline``) has no separate STT/TTS stage — the realtime
LLM handles audio-to-audio directly and only broadcasts a
``TranscriptionFrame`` downstream for logging. This processor can still
scrub what gets stored on that path (insert it right after the realtime LLM,
before ``pipeline_engine_callback_processor``), but it cannot stop raw audio
containing PII from reaching the remote realtime provider — that data has
already left as audio by the time any frame reaches this processor. That is
an audio-level problem, out of scope here just as it is for the original
piiguard ("Isn't: an audio-level redactor").
"""

from __future__ import annotations

from collections.abc import Callable

from loguru import logger
from pipecat.frames.frames import Frame, InterimTranscriptionFrame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from .redactor import Redactor
from .types import DetectedEntity

OnRedactedCallback = Callable[[str, str, list[DetectedEntity]], None]


class PIIRedactionProcessor(FrameProcessor):
    """Redacts PII in transcription frames as they pass through.

    Mutates ``frame.text`` in place and pushes the *same* frame object
    (rather than constructing a new one) so frame identity, ``pts``, and
    anything downstream keyed off frame identity keep working — the same
    pattern Dograh's own processors use (see ``recording_router_processor.py``).
    """

    def __init__(
        self,
        redactor: Redactor | None = None,
        *,
        redact_interim: bool = False,
        on_redacted: OnRedactedCallback | None = None,
        **kwargs,
    ):
        """
        Args:
            redactor: Shared Redactor instance. Use a ``RegexPIIDetector``
                (the default) for this hot path — Presidio is too slow to
                run per-turn here; save it for the stored-transcript hook.
            redact_interim: Also redact ``InterimTranscriptionFrame`` (partial,
                not-yet-finalized STT results), not just finalized
                ``TranscriptionFrame``. Off by default: interim results are
                often shown live in a UI and get re-sent repeatedly as the
                utterance is refined, so redacting them is extra work that
                does not change what ultimately reaches the LLM (the
                finalized frame is what ``user_context_aggregator`` consumes).
                Turn on only if your UI also surfaces interim text somewhere
                PII shouldn't be echoed.
            on_redacted: Optional ``callback(original_text, redacted_text, entities)``
                fired whenever a frame is modified. Useful for metrics/audit
                logging — do not use it to store the raw ``original_text``
                anywhere PII shouldn't go.
        """
        super().__init__(**kwargs)
        self._redactor = redactor or Redactor()
        self._redact_interim = redact_interim
        self._on_redacted = on_redacted

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        should_redact = isinstance(frame, TranscriptionFrame) or (
            self._redact_interim and isinstance(frame, InterimTranscriptionFrame)
        )
        if should_redact and frame.text:
            result = await self._redactor.redact(frame.text)
            if result.entities:
                logger.debug(
                    f"piiguard: redacted {len(result.entities)} entities "
                    f"({result.summary()}) from turn"
                )
                if self._on_redacted:
                    self._on_redacted(result.original, result.redacted, result.entities)
                frame.text = result.redacted

        await self.push_frame(frame, direction)
