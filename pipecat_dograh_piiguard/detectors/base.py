"""The detector interface.

:class:`PIIDetector` is a structural :class:`~typing.Protocol`, not a base
class you must inherit. Any object exposing a ``name`` and an awaitable
``detect`` satisfies it — a regex pack, an NER model, an LLM call, a managed
cloud API. A new backend drops in with **zero** changes to this package's
public types.

The protocol has no framework dependency, so a new backend never needs to
depend on Pipecat, Dograh, or anything else beyond the interface itself.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..types import DetectedEntity


@runtime_checkable
class PIIDetector(Protocol):
    """Detect personally-identifiable entities in a string of text.

    Implementations may be regex-based (fast, zero deps), NER-based (more
    accurate on names/orgs), or LLM-based (highest accuracy, highest cost).
    The :class:`~..redactor.Redactor` is agnostic to which.
    """

    @property
    def name(self) -> str:
        """Short identifier shown in events and logs (e.g. ``regex``, ``presidio``)."""
        ...

    async def detect(self, text: str) -> list[DetectedEntity]:
        """Return all PII entities found in ``text``.

        Entities reference character offsets in ``text``. Overlapping spans
        are allowed; the redactor's strategy deduplicates. ``entity_type``
        must use the neutral taxonomy and ``confidence`` must be in
        ``[0.0, 1.0]`` — translate any backend-specific encoding before
        returning.
        """
        ...
