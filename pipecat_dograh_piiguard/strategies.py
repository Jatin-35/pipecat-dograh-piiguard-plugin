"""Replacement strategies for redacted entities.

A strategy takes the original text + a list of detected entities and returns
the redacted text. Identical to ``livekit-plugins-piiguard``'s
``strategies.py`` — reused as-is since it has no LiveKit dependency.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Final, Literal

from .types import DetectedEntity

RedactionStrategy = Callable[[str, list[DetectedEntity]], str]

StrategyName = Literal["placeholder", "hash", "mask", "redact"]
"""The built-in strategy names. A closed set — a typo (``"placholder"``) is a
type error, not a silent ``ValueError`` at call time. Custom strategies use the
callable path, not a new string."""


def placeholder_strategy(
    text: str, entities: list[DetectedEntity], *, template: str = "[{entity_type}]"
) -> str:
    """Replace each entity span with ``[ENTITY_TYPE]``.

    Preserves conversational context — an LLM reading the redacted transcript
    still understands "the caller gave us their [EMAIL_ADDRESS]".
    """
    return _replace_spans(text, entities, lambda e: template.format(entity_type=e.entity_type))


def hash_strategy(
    text: str, entities: list[DetectedEntity], *, salt: str = "", prefix: str = "#"
) -> str:
    """Replace each entity with a deterministic short hash.

    Same input ⇒ same hash. Useful for de-duplication / linkage analysis
    without storing raw PII (e.g. "the same phone number appeared in 3 calls
    today"). Use a salt to prevent rainbow-table attacks on low-entropy values.
    """

    def _hash(e: DetectedEntity) -> str:
        h = hashlib.sha256((salt + e.text).encode("utf-8")).hexdigest()[:8]
        return f"{prefix}{e.entity_type}_{h}"

    return _replace_spans(text, entities, _hash)


def mask_strategy(
    text: str, entities: list[DetectedEntity], *, mask_char: str = "*", visible_tail: int = 0
) -> str:
    """Replace each entity with a fixed-width mask.

    Optionally keep the last ``visible_tail`` characters (e.g. ``****1234``
    for credit cards). Preserves string length, useful when downstream
    consumers expect fixed-format fields.
    """

    def _mask(e: DetectedEntity) -> str:
        if visible_tail > 0 and len(e.text) > visible_tail:
            return mask_char * (len(e.text) - visible_tail) + e.text[-visible_tail:]
        return mask_char * len(e.text)

    return _replace_spans(text, entities, _mask)


def redact_strategy(text: str, entities: list[DetectedEntity]) -> str:
    """Drop each entity span entirely (no placeholder, no mask)."""
    return _replace_spans(text, entities, lambda _: "")


STRATEGIES: Final[dict[StrategyName, RedactionStrategy]] = {
    "placeholder": placeholder_strategy,
    "hash": hash_strategy,
    "mask": mask_strategy,
    "redact": redact_strategy,
}


def resolve_strategy(strategy: StrategyName | RedactionStrategy) -> RedactionStrategy:
    """Look up a built-in strategy by name, or pass through a callable unchanged."""
    if callable(strategy):
        return strategy
    if strategy not in STRATEGIES:
        raise ValueError(
            f"Unknown strategy {strategy!r}. Known: {sorted(STRATEGIES)}. "
            "Pass a callable for custom strategies."
        )
    return STRATEGIES[strategy]


def _replace_spans(
    text: str, entities: list[DetectedEntity], replacement_fn: Callable[[DetectedEntity], str]
) -> str:
    """Substitute each entity span with ``replacement_fn(entity)``.

    Handles overlapping spans by taking the outermost (longest) match and
    discarding nested duplicates. Spans must reference offsets in ``text``.
    """
    if not entities:
        return text

    # Sort by start, longest-first on ties → drop strictly-nested duplicates
    ordered = sorted(entities, key=lambda e: (e.start, -e.end))
    deduped: list[DetectedEntity] = []
    last_end = -1
    for e in ordered:
        if e.start >= last_end:
            deduped.append(e)
            last_end = e.end
        # else: this span overlaps an earlier one — skip

    parts: list[str] = []
    cursor = 0
    for e in deduped:
        parts.append(text[cursor : e.start])
        parts.append(replacement_fn(e))
        cursor = e.end
    parts.append(text[cursor:])
    return "".join(parts)
