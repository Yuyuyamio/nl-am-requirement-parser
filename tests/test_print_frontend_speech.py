from __future__ import annotations

import tempfile
import unittest

from pathlib import Path
from types import SimpleNamespace

from am_print_frontend.speech import (
    SpeechTranscriber,
    SpeechTranscriptionError,
)


class _FakeModel:
    def __init__(self) -> None:
        self.audio_path: Path | None = None
        self.options: dict[str, object] = {}

    def transcribe(self, audio_path: str, **options: object):
        self.audio_path = Path(audio_path)
        self.options = options
        if not self.audio_path.is_file():
            raise AssertionError("temporary recording was not created")
        return (
            iter(
                [
                    SimpleNamespace(text="打印一个"),
                    SimpleNamespace(text="十厘米高的花瓶"),
                ]
            ),
            SimpleNamespace(
                language="zh",
                language_probability=0.98,
                duration=2.4,
            ),
        )


class TestSpeechTranscriber(unittest.TestCase):
    def test_transcribes_locally_and_removes_temporary_recording(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            model = _FakeModel()
            factory_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

            def factory(*args: object, **kwargs: object) -> _FakeModel:
                factory_calls.append((args, kwargs))
                return model

            transcriber = SpeechTranscriber(
                model_name="base",
                cache_directory=Path(temporary) / "models",
                model_factory=factory,
            )
            result = transcriber.transcribe(
                b"recorded-audio" * 20,
                content_type="audio/webm;codecs=opus",
                language="zh",
            )

            self.assertEqual(result["text"], "打印一个十厘米高的花瓶")
            self.assertEqual(result["language"], "zh")
            self.assertEqual(len(factory_calls), 1)
            self.assertEqual(model.options["language"], "zh")
            self.assertTrue(model.options["vad_filter"])
            self.assertIsNotNone(model.audio_path)
            self.assertFalse(model.audio_path.exists())

    def test_rejects_unsupported_recording_format(self) -> None:
        transcriber = SpeechTranscriber(model_factory=lambda *args, **kwargs: None)
        with self.assertRaises(SpeechTranscriptionError):
            transcriber.transcribe(
                b"not-audio" * 20,
                content_type="application/octet-stream",
            )

    def test_accepts_m4a_recording_alias(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            model = _FakeModel()
            transcriber = SpeechTranscriber(
                cache_directory=Path(temporary) / "models",
                model_factory=lambda *args, **kwargs: model,
            )

            result = transcriber.transcribe(
                b"m4a-recorded-audio" * 20,
                content_type="audio/x-m4a",
            )

            self.assertEqual(result["text"], "打印一个十厘米高的花瓶")
            self.assertEqual(model.audio_path.suffix, ".m4a")


if __name__ == "__main__":
    unittest.main()
