"""Data types used across pipecat-plugins-piiguard.

Framework-agnostic: no dependency on pipecat, Dograh, or any other host.
Identical in shape to ``livekit-plugins-piiguard``'s ``types.py`` — this is
the same engine, reused rather than re-derived.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

PIIEntityType = Literal[
    # Structured PII the regex backend validates locally
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "US_SSN",
    "US_ZIP_CODE",
    "DATE_OF_BIRTH",
    # Locale-scoped national identifiers (opt-in regex packs)
    "PL_POSTAL_CODE",
    "PL_PESEL",
    "PL_NIP",
    # PCI
    "CREDIT_CARD",
    "IBAN",
    # Network
    "IP_ADDRESS",
    "URL",
    "MAC_ADDRESS",
    # Secrets
    "AWS_ACCESS_KEY",
    "GITHUB_TOKEN",
    "JWT",
    "BITCOIN_ADDRESS",
    # Free-form entities a learned backend (e.g. Presidio) adds
    "PERSON",
    "LOCATION",
    "ORGANIZATION",
    "DATE_TIME",
    "US_PASSPORT",
    "US_DRIVER_LICENSE",
    "MEDICAL_LICENSE",
    "NRP",
    # Neutral catch-all for anything a backend reports outside this taxonomy
    "OTHER",
]
"""The neutral entity vocabulary. This is the closed set the built-in
detectors emit and the contract a backend adapter translates *into*.

:attr:`DetectedEntity.entity_type` itself stays ``str``: the detector
:class:`~.detectors.base.PIIDetector` protocol is deliberately open, so a
custom backend may emit a label outside this set. ``PIIEntityType`` enumerates
what this package ships and guarantees; it does not constrain third-party
detectors.
"""


@dataclass(frozen=True)
class DetectedEntity:
    """A single PII entity detected in text.

    Attributes:
        entity_type: Canonical entity name in ``UPPER_SNAKE_CASE``. Built-in
            detectors draw from the neutral :data:`PIIEntityType` taxonomy
            (e.g. ``EMAIL_ADDRESS``, ``PHONE_NUMBER``, ``CREDIT_CARD``,
            ``US_SSN``, ``PERSON``, ``IBAN``); the field is ``str`` because the
            detector protocol is open to custom backends.
        start: Character offset (inclusive) into the original text.
        end: Character offset (exclusive) into the original text.
        text: The matched substring. Populated on single-string
            :class:`RedactionResult` results (for audit / debugging), but
            blanked to ``""`` on entities inside a :class:`RedactedTranscript`
            unless ``keep_entity_text=True`` was set — so the transcript
            never carries raw PII into a compliance store.
        confidence: Detector confidence in ``[0.0, 1.0]``. Regex detectors
            with checksum validation typically return ``1.0``; NER-based
            detectors return their model probability.
        detector: Name of the detector that produced this entity.
    """

    entity_type: str
    start: int
    end: int
    text: str
    confidence: float = 1.0
    detector: str = "unknown"


@dataclass
class RedactionResult:
    """Outcome of redacting a single text string."""

    original: str
    """The raw input. Do not store this anywhere PII shouldn't go."""

    redacted: str
    """The redacted output safe for storage, logging, and downstream pipelines."""

    entities: list[DetectedEntity] = field(default_factory=list)
    """All entities detected in ``original`` (regardless of whether each was redacted)."""

    @property
    def has_pii(self) -> bool:
        return bool(self.entities)

    def summary(self) -> dict[str, int]:
        """``{entity_type: count}`` — useful for audit logs without leaking content."""
        out: dict[str, int] = {}
        for e in self.entities:
            out[e.entity_type] = out.get(e.entity_type, 0) + 1
        return out


@dataclass
class RedactedTranscript:
    """End-of-call payload: every message in the conversation, redacted.

    Designed to be the canonical artifact stored in your compliance archive /
    audit log / customer-data-export pipeline. Carries no raw PII by default:
    ``redacted`` text is scrubbed and every entity's ``text`` is blanked, so
    ``dataclasses.asdict(transcript)`` / ``json.dumps`` are safe to persist.
    (Opt back into raw values with ``keep_entity_text=True`` only if your
    audit store is allowed to hold PII.)
    """

    messages: list[RedactedMessage]
    """One entry per chat-context item, in conversation order."""

    entity_counts: dict[str, int] = field(default_factory=dict)
    """Aggregated entity counts across the whole transcript."""

    @property
    def has_pii(self) -> bool:
        return bool(self.entity_counts)

    @property
    def total_entities(self) -> int:
        return sum(self.entity_counts.values())

    def to_text(self) -> str:
        """Pretty-print the redacted transcript as plain text."""
        return "\n".join(f"{m.role}: {m.redacted}" for m in self.messages)


@dataclass
class RedactedMessage:
    """A single redacted chat-context entry."""

    role: str
    """``user`` / ``assistant`` / ``system`` / etc."""

    redacted: str
    """The redacted content. **Never** contains raw PII."""

    entities: list[DetectedEntity] = field(default_factory=list)
    """Entities found in the original content. Each entity's ``text`` is blanked by default (``keep_entity_text=False``) so this is safe to serialize; it only holds the raw substring when ``keep_entity_text=True`` was explicitly set."""

    metadata: dict[str, Any] = field(default_factory=dict)
    """Provider-specific metadata (timestamps, message IDs, etc.) passed through unchanged."""
