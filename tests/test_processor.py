import pytest
from pipecat.frames.frames import InterimTranscriptionFrame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection

from pipecat_dograh_piiguard.processor import PIIRedactionProcessor
from pipecat_dograh_piiguard.redactor import Redactor


@pytest.mark.asyncio
async def test_redacts_finalized_transcription_frame_in_place():
    processor = PIIRedactionProcessor(Redactor(strategy="placeholder"))
    frame = TranscriptionFrame(text="my email is jane@example.com", user_id="u1", timestamp="t0")

    await processor.process_frame(frame, FrameDirection.DOWNSTREAM)

    assert frame.text == "my email is [EMAIL_ADDRESS]"
    assert processor.pushed_frames == [(frame, FrameDirection.DOWNSTREAM)]


@pytest.mark.asyncio
async def test_leaves_interim_frame_untouched_by_default():
    processor = PIIRedactionProcessor(Redactor(strategy="placeholder"))
    frame = InterimTranscriptionFrame(
        text="my email is jane@example.com", user_id="u1", timestamp="t0"
    )

    await processor.process_frame(frame, FrameDirection.DOWNSTREAM)

    assert frame.text == "my email is jane@example.com"
    assert processor.pushed_frames == [(frame, FrameDirection.DOWNSTREAM)]


@pytest.mark.asyncio
async def test_redacts_interim_frame_when_opted_in():
    processor = PIIRedactionProcessor(Redactor(strategy="placeholder"), redact_interim=True)
    frame = InterimTranscriptionFrame(
        text="my email is jane@example.com", user_id="u1", timestamp="t0"
    )

    await processor.process_frame(frame, FrameDirection.DOWNSTREAM)

    assert frame.text == "my email is [EMAIL_ADDRESS]"


@pytest.mark.asyncio
async def test_on_redacted_callback_fires_with_original_and_redacted_text():
    seen = []
    processor = PIIRedactionProcessor(
        Redactor(strategy="placeholder"),
        on_redacted=lambda original, redacted, entities: seen.append((original, redacted, entities)),
    )
    frame = TranscriptionFrame(text="call 415-555-2671", user_id="u1", timestamp="t0")

    await processor.process_frame(frame, FrameDirection.DOWNSTREAM)

    assert len(seen) == 1
    original, redacted, entities = seen[0]
    assert original == "call 415-555-2671"
    assert redacted == "call [PHONE_NUMBER]"
    assert len(entities) == 1


@pytest.mark.asyncio
async def test_frame_without_pii_pushed_unchanged_and_no_callback():
    seen = []
    processor = PIIRedactionProcessor(
        Redactor(strategy="placeholder"),
        on_redacted=lambda *args: seen.append(args),
    )
    frame = TranscriptionFrame(text="hello there", user_id="u1", timestamp="t0")

    await processor.process_frame(frame, FrameDirection.DOWNSTREAM)

    assert frame.text == "hello there"
    assert seen == []
