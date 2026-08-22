"""The redaction orchestrator.

The :class:`Redactor` is the user-facing primitive: detector in, redacted
text out. It owns the policy decisions (which entity types to act on, which
replacement strategy to apply, what minimum confidence to require) so the
detectors stay focused on detection alone.

Framework-agnostic — no dependency on Pipecat or Dograh. Both the in-flight
``FrameProcessor`` (``dograh/processor.py``) and the stored-transcript hook
(``dograh/transcript_hook.py``) share this one facade, so detection config
and strategy stay consistent across both redaction points.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .detectors.base import PIIDetector
from .detectors.regex import RegexPIIDetector
from .strategies import RedactionStrategy, StrategyName, resolve_strategy
from .types import DetectedEntity, RedactedMessage, RedactedTranscript, RedactionResult


class Redactor:
    """Run a :class:`PIIDetector` over text and produce a :class:`RedactionResult`.

    Args:
        detector: PII detector to use. Defaults to a :class:`RegexPIIDetector`
            (zero deps, fast, conservative).
        strategy: Built-in strategy name (``"placeholder"``, ``"hash"``,
            ``"mask"``, ``"redact"``) or a custom callable.
        entity_types: Optionally restrict redaction to these entity types.
            ``None`` (default) redacts every entity the detector reports.
        min_confidence: Drop entities whose ``confidence`` is below this.
        keep_entity_text: When ``False`` (default), the raw matched
            substring (:attr:`DetectedEntity.text`) is blanked on every
            entity attached to a :class:`RedactedTranscript`, so the
            transcript is safe to serialize wholesale (``dataclasses.asdict``
            / ``json.dumps``) into a compliance store. Set ``True`` only if
            your audit pipeline needs the raw values and stores them
            somewhere PII is allowed.
    """

    def __init__(
        self,
        detector: PIIDetector | None = None,
        *,
        strategy: StrategyName | RedactionStrategy = "placeholder",
        entity_types: list[str] | None = None,
        min_confidence: float = 0.0,
        keep_entity_text: bool = False,
    ) -> None:
        self._detector = detector or RegexPIIDetector()
        self._strategy = resolve_strategy(strategy)
        self._entity_types = set(entity_types) if entity_types is not None else None
        self._min_confidence = min_confidence
        self._keep_entity_text = keep_entity_text

    @property
    def detector(self) -> PIIDetector:
        return self._detector

    async def redact(self, text: str) -> RedactionResult:
        """Detect PII in ``text`` and return the original + redacted versions."""
        if not text:
            return RedactionResult(original=text, redacted=text)

        entities = await self._detector.detect(text)
        to_redact = [
            e
            for e in entities
            if e.confidence >= self._min_confidence
            and (self._entity_types is None or e.entity_type in self._entity_types)
        ]
        redacted = self._strategy(text, to_redact)
        return RedactionResult(original=text, redacted=redacted, entities=entities)

    async def redact_messages(
        self, messages: Iterable[dict[str, Any]], *, content_key: str = "content"
    ) -> RedactedTranscript:
        """Redact every message in a chat-style transcript.

        Args:
            messages: Iterable of dicts with at least a ``role`` and a
                ``content`` (or ``content_key``) string field. Extra fields
                are preserved into the :attr:`RedactedMessage.metadata`.
            content_key: Override the field name where text lives.

        Returns:
            A :class:`RedactedTranscript` containing one entry per input
            message, plus an aggregated ``entity_counts`` summary. Unless
            ``keep_entity_text=True`` was passed to the constructor, every
            entity's raw ``text`` is blanked so the whole transcript is
            safe to serialize into a compliance store.
        """
        out_messages: list[RedactedMessage] = []
        counts: dict[str, int] = {}
        for msg in messages:
            content = msg.get(content_key) or ""
            role = str(msg.get("role", "unknown"))
            result = await self.redact(content)
            metadata = {k: v for k, v in msg.items() if k not in {"role", content_key}}
            entities = (
                result.entities if self._keep_entity_text else _strip_raw_text(result.entities)
            )
            out_messages.append(
                RedactedMessage(
                    role=role,
                    redacted=result.redacted,
                    entities=entities,
                    metadata=metadata,
                )
            )
            for ent in result.entities:
                counts[ent.entity_type] = counts.get(ent.entity_type, 0) + 1
        return RedactedTranscript(messages=out_messages, entity_counts=counts)


def _strip_raw_text(entities: list[DetectedEntity]) -> list[DetectedEntity]:
    """Return copies of ``entities`` with the ``text`` field blanked out.

    Useful before persisting a :class:`RedactedTranscript` to a compliance
    store — the offsets + entity_types are usually enough for audit, and
    keeping the raw matched substrings around defeats the point of redaction.
    """
    return [
        DetectedEntity(
            entity_type=e.entity_type,
            start=e.start,
            end=e.end,
            text="",
            confidence=e.confidence,
            detector=e.detector,
        )
        for e in entities
    ]
