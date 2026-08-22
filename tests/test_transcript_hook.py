from typing import Any

import pytest

from pipecat_dograh_piiguard.redactor import Redactor
from pipecat_dograh_piiguard.transcript_hook import RedactingTranscriptCoordinator


class _FakeCoordinator:
    """Stand-in for Dograh's TranscriptLogCoordinator: records every call it receives."""

    def __init__(self) -> None:
        self.user_calls: list[dict[str, Any]] = []
        self.assistant_calls: list[dict[str, Any]] = []
        self.turn_started_calls: list[int] = []

    async def record_user_transcript(self, *, text: str, **kwargs: Any) -> None:
        self.user_calls.append({"text": text, **kwargs})

    async def record_assistant_transcript(self, *, text: str, **kwargs: Any) -> None:
        self.assistant_calls.append({"text": text, **kwargs})

    async def record_turn_started(self, turn_id: int) -> None:
        self.turn_started_calls.append(turn_id)

    def attach_turn_tracking_observer(self, observer: Any) -> None:
        self._observer = observer


@pytest.mark.asyncio
async def test_redacts_user_transcript_before_forwarding():
    inner = _FakeCoordinator()
    coordinator = RedactingTranscriptCoordinator(inner, redactor=Redactor())

    await coordinator.record_user_transcript(
        text="my email is jane@example.com", timestamp="t0"
    )

    assert len(inner.user_calls) == 1
    assert inner.user_calls[0]["text"] == "my email is [EMAIL_ADDRESS]"
    assert inner.user_calls[0]["timestamp"] == "t0"


@pytest.mark.asyncio
async def test_redacts_assistant_transcript_before_forwarding():
    inner = _FakeCoordinator()
    coordinator = RedactingTranscriptCoordinator(inner, redactor=Redactor())

    await coordinator.record_assistant_transcript(
        text="I have your SSN as 123-45-6789 on file", timestamp="t1"
    )

    assert inner.assistant_calls[0]["text"] == "I have your SSN as [US_SSN] on file"


@pytest.mark.asyncio
async def test_non_text_methods_pass_through_unmodified():
    inner = _FakeCoordinator()
    coordinator = RedactingTranscriptCoordinator(inner, redactor=Redactor())

    await coordinator.record_turn_started(3)
    coordinator.attach_turn_tracking_observer(observer=object())

    assert inner.turn_started_calls == [3]
    assert hasattr(inner, "_observer")


@pytest.mark.asyncio
async def test_text_without_pii_passes_through_unchanged():
    inner = _FakeCoordinator()
    coordinator = RedactingTranscriptCoordinator(inner, redactor=Redactor())

    await coordinator.record_user_transcript(text="hello there", timestamp="t0")

    assert inner.user_calls[0]["text"] == "hello there"
