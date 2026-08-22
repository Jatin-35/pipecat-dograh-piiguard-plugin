"""Test-only stand-ins for the ``pipecat`` package.

``pipecat_dograh_piiguard.processor`` imports real classes from
``pipecat.frames.frames`` and ``pipecat.processors.frame_processor`` at
module scope. The actual package under test targets Dograh's fork
(``dograh-hq/pipecat``), which is vendored as a git submodule inside Dograh's
own repo rather than published to PyPI under a name this project can depend
on — so it isn't installed here.

These fakes implement just enough of the real interface (verified against
the actual fork's ``pipecat/src/pipecat/frames/frames.py`` and
``pipecat/src/pipecat/processors/frame_processor.py``) for
``PIIRedactionProcessor``'s own logic to be exercised honestly: a mutable
``TextFrame.text``, the ``process_frame(frame, direction)`` /
``push_frame(frame, direction)`` shape, and ``FrameDirection``. It does not
exercise Pipecat's real queuing/task machinery — that's Pipecat's contract to
test, not this plugin's.
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass
from enum import Enum


def _install_fake_pipecat() -> None:
    if "pipecat" in sys.modules:
        return

    pipecat = types.ModuleType("pipecat")
    frames_pkg = types.ModuleType("pipecat.frames")
    frames_mod = types.ModuleType("pipecat.frames.frames")
    processors_pkg = types.ModuleType("pipecat.processors")
    frame_processor_mod = types.ModuleType("pipecat.processors.frame_processor")

    class FrameDirection(Enum):
        DOWNSTREAM = 1
        UPSTREAM = 2

    @dataclass
    class Frame:
        pass

    @dataclass
    class TextFrame(Frame):
        text: str

    @dataclass
    class TranscriptionFrame(TextFrame):
        user_id: str = ""
        timestamp: str = ""

    @dataclass
    class InterimTranscriptionFrame(TextFrame):
        user_id: str = ""
        timestamp: str = ""

    class FrameProcessor:
        def __init__(self, **kwargs):
            self.pushed_frames: list[tuple[Frame, FrameDirection]] = []

        async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
            pass

        async def push_frame(
            self, frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM
        ) -> None:
            self.pushed_frames.append((frame, direction))

    frames_mod.Frame = Frame
    frames_mod.TextFrame = TextFrame
    frames_mod.TranscriptionFrame = TranscriptionFrame
    frames_mod.InterimTranscriptionFrame = InterimTranscriptionFrame

    frame_processor_mod.FrameDirection = FrameDirection
    frame_processor_mod.FrameProcessor = FrameProcessor

    sys.modules["pipecat"] = pipecat
    sys.modules["pipecat.frames"] = frames_pkg
    sys.modules["pipecat.frames.frames"] = frames_mod
    sys.modules["pipecat.processors"] = processors_pkg
    sys.modules["pipecat.processors.frame_processor"] = frame_processor_mod


_install_fake_pipecat()
