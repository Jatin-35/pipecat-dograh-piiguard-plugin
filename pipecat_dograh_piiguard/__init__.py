"""pipecat_dograh_piiguard — PII redaction for Dograh's Pipecat-based voice pipeline.

A detection engine (regex + optional Presidio) built against Dograh's actual
pipeline, a fork of Pipecat. See the package README for the two integration
points and ``examples/wiring_example.py`` for the exact diff against
Dograh's own pipeline code.

``PIIRedactionProcessor`` (in :mod:`.processor`) is deliberately **not**
imported here: it requires the real ``pipecat`` package importable at
runtime (present inside Dograh's own backend, not a dependency of this
package — see the README). Importing ``pipecat_dograh_piiguard`` itself,
and everything exported below, has zero dependencies. Import the processor
explicitly where you have pipecat on the path::

    from pipecat_dograh_piiguard.processor import PIIRedactionProcessor
"""

from __future__ import annotations

from .detectors import PIIDetector, RegexPIIDetector
from .redactor import Redactor
from .strategies import (
    RedactionStrategy,
    StrategyName,
    hash_strategy,
    mask_strategy,
    placeholder_strategy,
    redact_strategy,
    resolve_strategy,
)
from .transcript_hook import RedactingTranscriptCoordinator
from .types import (
    DetectedEntity,
    PIIEntityType,
    RedactedMessage,
    RedactedTranscript,
    RedactionResult,
)

__all__ = [
    "DetectedEntity",
    "PIIDetector",
    "PIIEntityType",
    "RedactedMessage",
    "RedactedTranscript",
    "RedactingTranscriptCoordinator",
    "RedactionResult",
    "RedactionStrategy",
    "Redactor",
    "RegexPIIDetector",
    "StrategyName",
    "hash_strategy",
    "mask_strategy",
    "placeholder_strategy",
    "redact_strategy",
    "resolve_strategy",
]
