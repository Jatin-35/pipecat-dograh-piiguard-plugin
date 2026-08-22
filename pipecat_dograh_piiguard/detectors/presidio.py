"""Microsoft Presidio adapter (optional, via the ``[presidio]`` extra).

Presidio uses spaCy / transformers for named-entity recognition, giving
better recall on free-form entities like names and organizations than the
regex detector. Cost: ~300 MB resident memory, ~10-50 ms per call — keep this
off the in-flight hot path (use it for the end-of-call / stored-transcript
redaction point instead).

Install (English)::

    pip install "pipecat-dograh-piiguard[presidio]"
    python -m spacy download en_core_web_lg

Non-English (e.g. Polish)::

    pip install "pipecat-dograh-piiguard[presidio]"
    python -m spacy download pl_core_news_lg
    # then: PresidioPIIDetector(language="pl")

``language=`` is self-sufficient: a matching spaCy NLP engine is
auto-provisioned and the model's native NER tagset is mapped onto Presidio's
entity vocabulary. If the requested language has no working analyzer the
detector **fails loudly** instead of silently redacting nothing — silent
zero-output is the worst failure mode for a redaction tool.

The class lazy-imports the Presidio packages so the rest of this package
remains importable without them.
"""

from __future__ import annotations

from typing import Any

from ..types import DetectedEntity, PIIEntityType
from .base import PIIDetector

# The translation boundary: Presidio's entity vocabulary -> the neutral
# taxonomy. Anything Presidio reports that has no neutral equivalent collapses
# to the catch-all below rather than leaking a vendor-specific label onto the
# public DetectedEntity.entity_type.
_NEUTRAL_FALLBACK: PIIEntityType = "OTHER"

_PRESIDIO_TO_PIIGUARD: dict[str, PIIEntityType] = {
    "EMAIL_ADDRESS": "EMAIL_ADDRESS",
    "PHONE_NUMBER": "PHONE_NUMBER",
    "CREDIT_CARD": "CREDIT_CARD",
    "IBAN_CODE": "IBAN",
    "US_SSN": "US_SSN",
    "US_PASSPORT": "US_PASSPORT",
    "US_DRIVER_LICENSE": "US_DRIVER_LICENSE",
    "IP_ADDRESS": "IP_ADDRESS",
    "URL": "URL",
    "PERSON": "PERSON",
    "LOCATION": "LOCATION",
    "ORGANIZATION": "ORGANIZATION",
    "DATE_TIME": "DATE_TIME",
    "MEDICAL_LICENSE": "MEDICAL_LICENSE",
    "NRP": "NRP",
}

# spaCy model naming: English ships *web* models, every other language ships
# *news* models. A caller can override via ``spacy_model=``.
_DEFAULT_SPACY_MODELS: dict[str, str] = {
    "en": "en_core_web_lg",
    "pl": "pl_core_news_lg",
    "de": "de_core_news_lg",
    "es": "es_core_news_lg",
    "fr": "fr_core_news_lg",
    "it": "it_core_news_lg",
    "pt": "pt_core_news_lg",
    "nl": "nl_core_news_lg",
}


def _resolve_spacy_model(language: str, override: str | None) -> str:
    if override:
        return override
    if language in _DEFAULT_SPACY_MODELS:
        return _DEFAULT_SPACY_MODELS[language]
    # spaCy convention for non-English; surfaces a clear "model not found"
    # later if the guess is wrong, rather than silently doing nothing.
    return f"{language}_core_news_lg"


# Per-language NER tagset -> Presidio entity. Polish ``pl_core_news_*`` uses
# the NKJP tagset (persName/placeName/geogName/orgName), none of which are in
# Presidio's default mapping, so names pass through unredacted unless we map
# them. English models use Presidio's built-in mapping (return ``None`` ->
# don't override).
_NER_LABEL_MAPPINGS: dict[str, dict[str, str]] = {
    "pl": {
        # NKJP tagset emitted by pl_core_news_*
        "persName": "PERSON",
        "placeName": "LOCATION",
        "geogName": "LOCATION",
        "orgName": "ORGANIZATION",
        "date": "DATE_TIME",
        "time": "DATE_TIME",
        # Some pl models additionally emit the universal CoNLL set
        "PER": "PERSON",
        "PERSON": "PERSON",
        "LOC": "LOCATION",
        "GPE": "LOCATION",
        "LOCATION": "LOCATION",
        "ORG": "ORGANIZATION",
        "ORGANIZATION": "ORGANIZATION",
        "DATE": "DATE_TIME",
        "TIME": "DATE_TIME",
    },
}


def _resolve_ner_mapping(language: str, override: dict[str, str] | None) -> dict[str, str] | None:
    if override is not None:
        return override
    return _NER_LABEL_MAPPINGS.get(language)


class PresidioPIIDetector(PIIDetector):
    """Presidio-backed detector. Higher accuracy on names, higher cost.

    Args:
        language: ISO 639-1 code (default ``"en"``). For non-English a
            matching spaCy NLP engine is auto-provisioned; you only need the
            spaCy model installed (``python -m spacy download <model>``).
        score_threshold: Minimum Presidio confidence to accept
            (``0.0``-``1.0``). Presidio's defaults favor recall; raise this
            if you see too many false positives.
        analyzer: Optionally pass a fully built ``AnalyzerEngine`` (e.g. with
            custom recognizers). When provided it is used verbatim; the
            language-support guard still applies.
        spacy_model: Override the spaCy model name. Defaults to a
            per-language convention (``en_core_web_lg`` /
            ``<lang>_core_news_lg``).
        ner_entity_mapping: Override the model-NER-tagset -> Presidio-entity
            map. Defaults to a shipped mapping for supported locales (Polish
            NKJP today); ``None`` for English (Presidio's built-in mapping).

    Raises:
        RuntimeError: at first use, if the analyzer has no support for
            ``language`` (instead of silently detecting nothing).
        ImportError: if the ``presidio`` extra isn't installed.
    """

    def __init__(
        self,
        *,
        language: str = "en",
        score_threshold: float = 0.5,
        analyzer: Any = None,
        spacy_model: str | None = None,
        ner_entity_mapping: dict[str, str] | None = None,
    ) -> None:
        self._language = language
        self._score_threshold = score_threshold
        self._analyzer = analyzer
        self._spacy_model = spacy_model
        self._ner_entity_mapping = ner_entity_mapping
        self._validated = False

    @property
    def name(self) -> str:
        return "presidio"

    def _build_analyzer(self) -> Any:
        try:
            from presidio_analyzer import AnalyzerEngine
        except ImportError as exc:
            raise ImportError(
                "presidio-analyzer is required for PresidioPIIDetector. "
                'Install with `pip install "pipecat-dograh-piiguard[presidio]"`.'
            ) from exc

        # English with no overrides keeps Presidio's fast default path.
        if (
            self._language == "en"
            and self._spacy_model is None
            and self._ner_entity_mapping is None
        ):
            return AnalyzerEngine()

        from presidio_analyzer.nlp_engine import NlpEngineProvider

        model_name = _resolve_spacy_model(self._language, self._spacy_model)
        mapping = _resolve_ner_mapping(self._language, self._ner_entity_mapping)

        nlp_configuration: dict[str, Any] = {
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": self._language, "model_name": model_name}],
        }
        if mapping is not None:
            nlp_configuration["ner_model_configuration"] = {
                "model_to_presidio_entity_mapping": mapping
            }

        try:
            nlp_engine = NlpEngineProvider(nlp_configuration=nlp_configuration).create_engine()
        except (OSError, ImportError) as exc:
            raise RuntimeError(
                f"piiguard: could not load spaCy model {model_name!r} for "
                f"language {self._language!r}. Install it with "
                f"`python -m spacy download {model_name}` "
                "(or pass spacy_model= / a prebuilt analyzer=)."
            ) from exc

        return AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=[self._language])

    def _ensure_analyzer(self) -> Any:
        if self._analyzer is None:
            self._analyzer = self._build_analyzer()
        if not self._validated:
            self._assert_language_supported(self._analyzer)
            self._validated = True
        return self._analyzer

    def _assert_language_supported(self, analyzer: Any) -> None:
        """Fail loud when the analyzer can't serve ``language``.

        A default ``AnalyzerEngine()`` only supports English; calling
        ``analyze(language="pl")`` on it yields zero entities and no error —
        the worst outcome for a redaction tool. Refuse instead.
        """
        supported = getattr(analyzer, "supported_languages", None)
        if supported is not None and self._language not in supported:
            raise RuntimeError(
                f"piiguard: Presidio analyzer does not support language "
                f"{self._language!r} (supports {list(supported)!r}). A default "
                "AnalyzerEngine is English-only — pass language= so this "
                "package auto-provisions a matching spaCy engine, or inject a "
                "correctly built analyzer=. Refusing rather than silently "
                "redacting nothing."
            )

    async def detect(self, text: str) -> list[DetectedEntity]:
        if not text:
            return []
        analyzer = self._ensure_analyzer()
        results = analyzer.analyze(text=text, language=self._language)
        out: list[DetectedEntity] = []
        for r in results:
            if r.score < self._score_threshold:
                continue
            entity_type = _PRESIDIO_TO_PIIGUARD.get(r.entity_type, _NEUTRAL_FALLBACK)
            out.append(
                DetectedEntity(
                    entity_type=entity_type,
                    start=r.start,
                    end=r.end,
                    text=text[r.start : r.end],
                    confidence=float(r.score),
                    detector=self.name,
                )
            )
        return out
