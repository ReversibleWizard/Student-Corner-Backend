"""
VoiceAgentService — streams recorded audio to the ElevenLabs
Voice Delivery Coach agent and returns both transcript and
delivery review from a single call.
"""
from __future__ import annotations

import os
import time
import threading
import tempfile

import numpy as np
import soundfile as sf
from elevenlabs import ElevenLabs
from elevenlabs.conversational_ai.conversation import Conversation, AudioInterface

from ai_interviewer.exceptions import (
    VoiceAgentError, VoiceAgentNotConfiguredError, TranscriptionError, InvalidInputError,
)
from ai_interviewer.logger import get_logger

log = get_logger(__name__)

AGENT_TIMEOUT_S = 30
MAX_AUDIO_BYTES = 25 * 1024 * 1024   # 25 MB


class FileAudioInterface(AudioInterface):
    """Replays a pre-recorded file into the ElevenLabs agent pipeline."""

    TARGET_SR    = 16_000
    CHUNK_FRAMES = 1_600

    def __init__(self, audio_bytes: bytes, suffix: str = ".webm"):
        self._audio_bytes = audio_bytes
        self._suffix      = suffix
        self._stop_event  = threading.Event()

    def start(self, input_callback):
        def _stream():
            tmp_path: str | None = None
            try:
                with tempfile.NamedTemporaryFile(suffix=self._suffix, delete=False) as tmp:
                    tmp.write(self._audio_bytes)
                    tmp_path = tmp.name

                audio, sr = sf.read(tmp_path, dtype="int16", always_2d=False)

                if sr != self.TARGET_SR:
                    from scipy.signal import resample as sp_resample
                    audio = sp_resample(
                        audio, int(len(audio) * self.TARGET_SR / sr)
                    ).astype(np.int16)
                if audio.ndim > 1:
                    audio = audio[:, 0]

                for i in range(0, len(audio), self.CHUNK_FRAMES):
                    if self._stop_event.is_set():
                        break
                    input_callback(audio[i : i + self.CHUNK_FRAMES].tobytes())
                    time.sleep(self.CHUNK_FRAMES / self.TARGET_SR)

                input_callback(np.zeros(self.TARGET_SR, dtype=np.int16).tobytes())

            except sf.SoundFileError as exc:
                log.error("FileAudioInterface decode error (%s): %s", self._suffix, exc)
            except Exception as exc:
                log.error("FileAudioInterface unexpected error: %s", exc)
            finally:
                if tmp_path:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass

        threading.Thread(target=_stream, daemon=True).start()

    def stop(self):
        self._stop_event.set()

    def output(self, audio: bytes):
        pass

    def interrupt(self):
        pass


class VoiceAgentService:
    """
    Wraps the ElevenLabs Conversational Agent for voice delivery analysis.
    analyze() → (transcript, delivery_review)
    """

    REVIEW_HEADER = (
        "### 🎙️ Voice Delivery Review\n\n"
        "> *Analysed by ElevenLabs Voice Delivery Coach — "
        "hears your actual tone, energy & emotion*\n\n"
    )
    FALLBACK_REVIEW = (
        "⚠️ Voice delivery review is temporarily unavailable. "
        "Your answer was still processed successfully."
    )

    def __init__(self):
        self._client: ElevenLabs | None = None
        self.agent_id = os.getenv("ELEVENLABS_AGENT_ID")

    @property
    def client(self) -> ElevenLabs:
        if self._client is None:
            api_key = os.getenv("ELEVENLABS_API_KEY")
            if not api_key:
                raise VoiceAgentError("ELEVENLABS_API_KEY is not set.")
            self._client = ElevenLabs(api_key=api_key)
        return self._client

    def _validate_audio(self, audio_bytes: bytes) -> None:
        if not audio_bytes:
            raise InvalidInputError("Audio payload is empty.")
        if len(audio_bytes) > MAX_AUDIO_BYTES:
            raise InvalidInputError(
                f"Audio too large ({len(audio_bytes)/1024/1024:.1f} MB). "
                f"Max: {MAX_AUDIO_BYTES//1024//1024} MB."
            )

    def analyze(self, audio_bytes: bytes, suffix: str = ".webm") -> tuple[str, str]:
        """
        Stream audio to the ElevenLabs agent.
        Returns (transcript, delivery_review).
        Falls back gracefully on timeout or non-fatal errors.
        Raises TranscriptionError if no transcript is returned at all.
        """
        if not self.agent_id:
            raise VoiceAgentNotConfiguredError()

        self._validate_audio(audio_bytes)

        transcript_parts: list[str] = []
        review_parts:     list[str] = []
        error_holder:     list[Exception] = []
        done = threading.Event()

        try:
            conv = Conversation(
                client          = self.client,
                agent_id        = self.agent_id,
                audio_interface = FileAudioInterface(audio_bytes, suffix),
                callback_user_transcript    = lambda t: transcript_parts.append(t),
                callback_agent_response     = lambda r: review_parts.append(r),
                callback_conversation_ended = lambda: done.set(),
            )
            conv.start_session()
            timed_out = not done.wait(timeout=AGENT_TIMEOUT_S)
            conv.end_session()
        except VoiceAgentNotConfiguredError:
            raise
        except Exception as exc:
            log.error("VoiceAgentService failed to start session: %s", exc)
            raise VoiceAgentError(reason=str(exc)) from exc

        if timed_out:
            log.warning("VoiceAgentService timed out after %ss — returning fallback.", AGENT_TIMEOUT_S)
            return "", self.FALLBACK_REVIEW

        if error_holder:
            log.error("VoiceAgent runtime error: %s", error_holder[0])
            return "", self.FALLBACK_REVIEW

        transcript = " ".join(transcript_parts).strip()
        review     = " ".join(review_parts).strip()

        if not transcript:
            raise TranscriptionError(
                reason="Agent returned empty transcript. "
                       "Audio may be silent, too short, or unsupported format."
            )

        if not review:
            log.warning("VoiceAgent returned no delivery review — using fallback.")
            review = self.FALLBACK_REVIEW

        log.info("VoiceAgent OK — transcript=%d chars review=%d chars", len(transcript), len(review))
        return transcript, review

    def format_review(self, agent_text: str) -> str:
        return self.REVIEW_HEADER + agent_text