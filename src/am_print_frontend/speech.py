from __future__ import annotations

import importlib.util
import os
import re
import tempfile
import threading

from pathlib import Path
from typing import Any, Callable


class SpeechTranscriptionError(RuntimeError):
    """A local recording could not be converted into editable text."""


class SpeechTranscriber:
    """Lazy, process-local faster-whisper adapter.

    The model is loaded once and reused. Audio is written to a uniquely named
    temporary file only for decoding and is deleted after every request.
    """

    _suffixes = {
        "audio/webm": ".webm",
        "audio/ogg": ".ogg",
        "audio/mp4": ".m4a",
        "audio/x-m4a": ".m4a",
        "audio/aac": ".aac",
        "audio/mpeg": ".mp3",
        "audio/mp3": ".mp3",
        "audio/x-mpeg": ".mp3",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/wave": ".wav",
        "audio/vnd.wave": ".wav",
    }

    def __init__(
        self,
        *,
        model_name: str | None = None,
        cache_directory: Path | str | None = None,
        device: str | None = None,
        compute_type: str | None = None,
        model_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.model_name = model_name or os.environ.get("AM_STT_MODEL", "base")
        self.cache_directory = (
            Path(cache_directory).expanduser().resolve()
            if cache_directory is not None
            else Path("outputs/speech_models").resolve()
        )
        self.device = device or os.environ.get("AM_STT_DEVICE", "cpu")
        self.compute_type = compute_type or os.environ.get(
            "AM_STT_COMPUTE_TYPE",
            "int8",
        )
        self._model_factory = model_factory
        self._model: Any | None = None
        self._model_lock = threading.Lock()
        self._transcription_lock = threading.Lock()

    @property
    def available(self) -> bool:
        return self._model_factory is not None or importlib.util.find_spec(
            "faster_whisper"
        ) is not None

    def capabilities(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "engine": "faster-whisper",
            "model": self.model_name,
            "device": self.device,
            "runs_locally": True,
            "editable_result": True,
        }

    def _get_model(self) -> Any:
        if self._model is not None:
            return self._model
        with self._model_lock:
            if self._model is not None:
                return self._model
            if self._model_factory is None:
                try:
                    from faster_whisper import WhisperModel
                except ImportError as exc:
                    raise SpeechTranscriptionError(
                        "本地语音组件尚未安装，请安装项目的语音依赖后重启应用。"
                    ) from exc
                factory: Callable[..., Any] = WhisperModel
            else:
                factory = self._model_factory
            self.cache_directory.mkdir(parents=True, exist_ok=True)
            try:
                self._model = factory(
                    self.model_name,
                    device=self.device,
                    compute_type=self.compute_type,
                    download_root=str(self.cache_directory),
                )
            except Exception as exc:
                raise SpeechTranscriptionError(
                    "本地语音模型加载失败；首次使用需要联网下载模型："
                    + str(exc)
                ) from exc
        return self._model

    def transcribe(
        self,
        audio: bytes,
        *,
        content_type: str,
        language: str | None = "zh",
    ) -> dict[str, Any]:
        mime_type = content_type.split(";", 1)[0].strip().lower()
        suffix = self._suffixes.get(mime_type)
        if suffix is None:
            raise SpeechTranscriptionError(
                f"不支持的录音格式：{mime_type or 'unknown'}"
            )
        if len(audio) < 128:
            raise SpeechTranscriptionError("录音内容太短，请重新说一次。")

        descriptor, temporary_name = tempfile.mkstemp(
            prefix="am-print-speech-",
            suffix=suffix,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(audio)
                handle.flush()
                os.fsync(handle.fileno())
            model = self._get_model()
            try:
                with self._transcription_lock:
                    segments, info = model.transcribe(
                        str(temporary_path),
                        language=language,
                        beam_size=5,
                        vad_filter=True,
                        vad_parameters={"min_silence_duration_ms": 500},
                        condition_on_previous_text=False,
                    )
                    segment_list = list(segments)
            except Exception as exc:
                raise SpeechTranscriptionError(
                    "录音转写失败：" + str(exc)
                ) from exc
            text = re.sub(
                r"\s+",
                " ",
                " ".join(str(segment.text).strip() for segment in segment_list),
            ).strip()
            text = re.sub(
                r"(?<=[\u3400-\u9fff，。！？；：])\s+"
                r"(?=[\u3400-\u9fff，。！？；：])",
                "",
                text,
            )
            if not text:
                raise SpeechTranscriptionError(
                    "没有识别到清晰语音，请靠近麦克风再试一次。"
                )
            detected_language = str(getattr(info, "language", language or ""))
            probability = float(getattr(info, "language_probability", 0.0))
            duration = float(getattr(info, "duration", 0.0))
            return {
                "text": text,
                "language": detected_language,
                "language_probability": probability,
                "duration_seconds": duration,
                "engine": "faster-whisper",
                "model": self.model_name,
            }
        finally:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
