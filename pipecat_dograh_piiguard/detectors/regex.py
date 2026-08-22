"""Zero-dependency regex-based PII detector.

Designed for the streaming voice-agent use case: fast (sub-millisecond),
local (no network calls, no models, no GPU), and predictable. Covers the
structured-entity types where regex works well; for free-form names / orgs,
plug in a learned detector via the :class:`~..detectors.base.PIIDetector`
protocol (Presidio adapter ships in this package).

Patterns are organized into **locale packs** so a non-US deployment is not
forced to redact US ZIP codes (and miss its own national identifiers):

    - ``core`` — locale-neutral structured PII (email, card, IBAN, IP, MAC,
      phone, URL, crypto, cloud secrets, ISO/EU dates).
    - ``us``   — US-specific (``US_SSN``, ``US_ZIP_CODE``).
    - ``pl``   — Polish national IDs (postal ``NN-NNN``, PESEL, NIP), each
      checksum-validated.

The default is ``("core", "us")`` (unchanged behavior for existing US/EN
callers). A Polish deployment uses ``packs=("core", "pl")``.

Patterns are validated where possible — Luhn (cards), mod-97 (IBAN), SSN-area
rules, PESEL/NIP checksums. ``US_ZIP_CODE`` is *context-anchored*: a bare
5-digit token is only treated as a ZIP when ZIP+4 form is used or a
ZIP/postal keyword or US state precedes it, so order numbers and foreign
postal codes are not silently redacted as ``[US_ZIP_CODE]``.

This is the same engine ``livekit-plugins-piiguard`` ships — reused verbatim
since it has no LiveKit dependency, per the design goal of not
reimplementing detection logic that already exists and is tested.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from ..types import DetectedEntity, PIIEntityType
from .base import PIIDetector

#: ``(matched_text) -> bool``
ValidatorFn = Callable[[str], bool]
#: ``(matched_text, full_text, start, end) -> bool`` — sees surrounding context
ContextValidatorFn = Callable[[str, str, int, int], bool]

PackName = str


@dataclass(frozen=True)
class _Pattern:
    entity_type: PIIEntityType
    regex: re.Pattern[str]
    pack: PackName = "core"
    validator: ValidatorFn | None = None
    context_validator: ContextValidatorFn | None = None


# --- validators -------------------------------------------------------------


def _luhn_valid(digits: str) -> bool:
    digits = re.sub(r"\D", "", digits)
    if len(digits) < 13 or len(digits) > 19:
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _iban_valid(iban: str) -> bool:
    iban = re.sub(r"\s", "", iban.upper())
    if not 15 <= len(iban) <= 34:
        return False
    if not re.match(r"^[A-Z]{2}[0-9]{2}[A-Z0-9]+$", iban):
        return False
    rearranged = iban[4:] + iban[:4]
    numeric = "".join(str(ord(c) - 55) if c.isalpha() else c for c in rearranged)
    try:
        return int(numeric) % 97 == 1
    except ValueError:
        return False


def _ssn_valid(ssn: str) -> bool:
    digits = re.sub(r"\D", "", ssn)
    if len(digits) != 9:
        return False
    area, group, serial = digits[:3], digits[3:5], digits[5:]
    if area in ("000", "666") or area.startswith("9"):
        return False
    if group == "00" or serial == "0000":
        return False
    return True


def _pesel_valid(pesel: str) -> bool:
    digits = re.sub(r"\D", "", pesel)
    if len(digits) != 11 or len(set(digits)) == 1:
        return False
    weights = (1, 3, 7, 9, 1, 3, 7, 9, 1, 3)
    s = sum(int(digits[i]) * weights[i] for i in range(10))
    control = (10 - s % 10) % 10
    if control != int(digits[10]):
        return False
    # Structural sanity: month digits encode a valid month with century offset.
    month = int(digits[2:4])
    return month % 20 in range(1, 13)


def _nip_valid(nip: str) -> bool:
    digits = re.sub(r"\D", "", nip)
    if len(digits) != 10 or len(set(digits)) == 1:
        return False
    weights = (6, 5, 7, 2, 3, 4, 5, 6, 7)
    s = sum(int(digits[i]) * weights[i] for i in range(9))
    check = s % 11
    return check != 10 and check == int(digits[9])


# --- US ZIP context anchoring -----------------------------------------------

_US_STATES = frozenset(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS "
    "MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV "
    "WI WY DC".split()
)
_ZIP_KEYWORD_RE = re.compile(r"(?i)\b(?:zip(?:\s*code)?|postal\s*code|post\s*code)\b")
# A US-state token must be an isolated, upper-case 2-letter word sitting
# immediately before the ZIP (e.g. "CA 94105"). Matching any 2-letter
# substring would fire on "refere[nc]e 48200" → NC → false ZIP.
_ZIP_STATE_RE = re.compile(r"\b([A-Z]{2})[ ,]*$")


def _zip_context_ok(matched: str, text: str, start: int, _end: int) -> bool:
    """Bare 5-digit numbers are not unconditionally a US ZIP.

    Accept only when the value is the distinctive ZIP+4 form, or a
    ZIP/postal keyword or a US-state token sits just before it. This kills
    the false-positive epidemic where order numbers / foreign postal codes
    were redacted as ``[US_ZIP_CODE]``.
    """
    if "-" in matched:  # ZIP+4 (e.g. 12345-6789) — distinctive enough alone
        return True
    if _ZIP_KEYWORD_RE.search(text[max(0, start - 25) : start]):
        return True
    m = _ZIP_STATE_RE.search(text[max(0, start - 10) : start])
    return bool(m and m.group(1) in _US_STATES)


# --- pattern table ----------------------------------------------------------
#
# Single ordered source of truth. Order matters: span deduplication keeps the
# earliest pattern on an exact-span tie, so specific/validated patterns must
# precede broad ones. PHONE_NUMBER is greedy (its digit-group regex also
# matches dotted IPs, ZIP+4, dotted dates, PL postal/PESEL/NIP), so every
# structured numeric type is listed ahead of it.

_PATTERNS: tuple[_Pattern, ...] = (
    _Pattern(
        "EMAIL_ADDRESS",
        re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
        pack="core",
    ),
    _Pattern(
        "US_SSN",
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b|\b\d{9}\b"),
        pack="us",
        validator=_ssn_valid,
    ),
    _Pattern(
        "CREDIT_CARD",
        re.compile(r"\b(?:\d[ \-]?){13,19}\b"),
        pack="core",
        validator=_luhn_valid,
    ),
    _Pattern(
        "IBAN",
        re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{2,4}){2,7}\b"),
        pack="core",
        validator=_iban_valid,
    ),
    _Pattern(
        "IP_ADDRESS",
        re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\b"),
        pack="core",
    ),
    _Pattern(
        "PL_POSTAL_CODE",
        re.compile(r"\b\d{2}-\d{3}\b"),
        pack="pl",
    ),
    _Pattern(
        "PL_PESEL",
        re.compile(r"\b\d{11}\b"),
        pack="pl",
        validator=_pesel_valid,
    ),
    _Pattern(
        "PL_NIP",
        re.compile(r"\b\d{3}-\d{3}-\d{2}-\d{2}\b|\b\d{3}-\d{2}-\d{2}-\d{3}\b|\b\d{10}\b"),
        pack="pl",
        validator=_nip_valid,
    ),
    _Pattern(
        "US_ZIP_CODE",
        re.compile(r"\b\d{5}-\d{4}\b|\b\d{5}\b"),
        pack="us",
        context_validator=_zip_context_ok,
    ),
    _Pattern(
        "DATE_OF_BIRTH",
        re.compile(
            r"\b(?:19|20)\d{2}[-/.](?:0[1-9]|1[0-2])[-/.](?:0[1-9]|[12]\d|3[01])\b"
            r"|\b(?:0[1-9]|1[0-2])[-/.](?:0[1-9]|[12]\d|3[01])[-/.](?:19|20)\d{2}\b"
            r"|\b(?:0[1-9]|[12]\d|3[01])[-/.](?:0[1-9]|1[0-2])[-/.](?:19|20)\d{2}\b"
        ),
        pack="core",
    ),
    _Pattern(
        "MAC_ADDRESS",
        re.compile(r"\b(?:[0-9A-Fa-f]{2}[:\-]){5}[0-9A-Fa-f]{2}\b"),
        pack="core",
    ),
    _Pattern(
        "PHONE_NUMBER",
        re.compile(
            r"(?:(?<!\d)(?:\+?\d{1,3}[\s.\-]?)?(?:\(\d{1,4}\)[\s.\-]?|\d{1,4}[\s.\-]?){2,4}\d{2,4}(?!\d))"
        ),
        pack="core",
        validator=lambda m: 7 <= len(re.sub(r"\D", "", m)) <= 15,
    ),
    _Pattern(
        "URL",
        re.compile(r"\bhttps?://[^\s<>'\"]+", re.IGNORECASE),
        pack="core",
    ),
    _Pattern(
        "BITCOIN_ADDRESS",
        re.compile(r"\b(?:bc1|[13])[A-HJ-NP-Za-km-z1-9]{25,87}\b"),
        pack="core",
    ),
    _Pattern(
        "AWS_ACCESS_KEY",
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        pack="core",
    ),
    _Pattern(
        "GITHUB_TOKEN",
        re.compile(r"\bghp_[A-Za-z0-9]{36}\b|\bgho_[A-Za-z0-9]{36}\b"),
        pack="core",
    ),
    _Pattern(
        "JWT",
        re.compile(r"\beyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b"),
        pack="core",
    ),
)

#: Packs a caller can select. ``all`` is the union of every shipped pattern.
PACKS: frozenset[PackName] = frozenset({"core", "us", "pl"})
DEFAULT_PACKS: tuple[PackName, ...] = ("core", "us")


_GROUP_ALIASES: dict[str, frozenset[str]] = {
    "pii": frozenset(
        {
            "EMAIL_ADDRESS",
            "PHONE_NUMBER",
            "US_SSN",
            "CREDIT_CARD",
            "IBAN",
            "US_ZIP_CODE",
            "DATE_OF_BIRTH",
            "PL_POSTAL_CODE",
            "PL_PESEL",
            "PL_NIP",
        }
    ),
    "pci": frozenset({"CREDIT_CARD", "IBAN"}),
    "secrets": frozenset({"AWS_ACCESS_KEY", "GITHUB_TOKEN", "JWT"}),
    "network": frozenset({"IP_ADDRESS", "URL", "MAC_ADDRESS"}),
    "all": frozenset({p.entity_type for p in _PATTERNS}),
}


class RegexPIIDetector(PIIDetector):
    """Local zero-dependency PII detector.

    Args:
        entity_types: Limit detection to these entity types. Accepts both
            specific types (``"EMAIL_ADDRESS"``) and group aliases (``"pii"``,
            ``"pci"``, ``"secrets"``, ``"network"``, ``"all"``). ``None``
            (default) detects every entity in the active packs.
        packs: Locale packs to activate. Defaults to ``("core", "us")`` —
            unchanged behavior for existing US/EN callers. A Polish
            deployment uses ``("core", "pl")`` to drop US ZIP/SSN noise and
            gain PESEL / NIP / PL postal detection. ``"all"`` activates every
            pack.
        min_confidence: Drop entities below this confidence. Validated /
            context-anchored hits score 1.0; unvalidated hits score 0.85.
    """

    def __init__(
        self,
        entity_types: list[str] | None = None,
        *,
        packs: tuple[PackName, ...] | list[PackName] | None = None,
        min_confidence: float = 0.0,
    ) -> None:
        self._allowed = self._resolve_types(entity_types)
        self._active = self._resolve_patterns(packs)
        self._min_confidence = min_confidence

    @property
    def name(self) -> str:
        return "regex"

    async def detect(self, text: str) -> list[DetectedEntity]:
        if not text:
            return []
        results: list[DetectedEntity] = []
        for pat in self._active:
            if self._allowed is not None and pat.entity_type not in self._allowed:
                continue
            for m in pat.regex.finditer(text):
                matched = m.group(0)
                confidence = 0.85
                if pat.context_validator is not None:
                    try:
                        if not pat.context_validator(matched, text, m.start(), m.end()):
                            continue
                    except Exception:
                        continue
                    confidence = 1.0
                elif pat.validator is not None:
                    try:
                        if not pat.validator(matched):
                            continue
                    except Exception:
                        continue
                    confidence = 1.0
                if confidence < self._min_confidence:
                    continue
                results.append(
                    DetectedEntity(
                        entity_type=pat.entity_type,
                        start=m.start(),
                        end=m.end(),
                        text=matched,
                        confidence=confidence,
                        detector=self.name,
                    )
                )
        return results

    @staticmethod
    def _resolve_patterns(
        packs: tuple[PackName, ...] | list[PackName] | None,
    ) -> tuple[_Pattern, ...]:
        if packs is None:
            selected: set[PackName] = set(DEFAULT_PACKS)
        elif "all" in packs:
            selected = set(PACKS)
        else:
            unknown = {p for p in packs if p not in PACKS}
            if unknown:
                raise ValueError(
                    f"Unknown regex pack(s) {sorted(unknown)}. Known: {sorted(PACKS)} (or 'all')."
                )
            selected = set(packs)
        # Preserve the canonical ordering from _PATTERNS (dedup depends on it).
        return tuple(p for p in _PATTERNS if p.pack in selected)

    @staticmethod
    def _resolve_types(types: list[str] | None) -> set[str] | None:
        if types is None:
            return None
        expanded: set[str] = set()
        for t in types:
            key = t.lower()
            if key in _GROUP_ALIASES:
                expanded.update(_GROUP_ALIASES[key])
            else:
                expanded.add(t)
        return expanded
