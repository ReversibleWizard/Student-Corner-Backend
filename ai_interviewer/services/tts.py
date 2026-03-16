import os
import re
import time
import threading
from elevenlabs import ElevenLabs
from elevenlabs.play import play

from ai_interviewer.exceptions import TTSError
from ai_interviewer.logger import get_logger

log = get_logger(__name__)

MAX_RETRIES   = 3
RETRY_DELAY_S = 1.5


class TTSService:
    """Handles all Text-to-Speech operations via ElevenLabs."""

    DEFAULT_VOICE_ID = "onwK4e9ZLuTAKqWW03F9"
    TTS_MODEL        = "eleven_multilingual_v2"

    def __init__(self):
        self._client: ElevenLabs | None = None
        self.voice_id = os.getenv("ELEVENLABS_VOICE_ID", self.DEFAULT_VOICE_ID)

    @property
    def client(self) -> ElevenLabs:
        if self._client is None:
            api_key = os.getenv("ELEVENLABS_API_KEY")
            if not api_key:
                raise TTSError("ELEVENLABS_API_KEY is not set.")
            self._client = ElevenLabs(api_key=api_key)
        return self._client

    def strip_markdown(self, text: str) -> str:
        text = re.sub(r"#{1,6}\s*", "", text)
        text = re.sub(r"\*{1,2}([^*]+)\*{1,2}", r"\1", text)
        text = re.sub(r"`[^`]*`", "", text)
        text = re.sub(r"---+", "", text)
        text = re.sub(r"\n{2,}", ". ", text)
        return text.strip()

    def _convert_with_retry(self, text: str) -> bytes:
        clean     = self.strip_markdown(text)
        last_exc: Exception | None = None

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                chunks = self.client.text_to_speech.convert(
                    text=clean, voice_id=self.voice_id, model_id=self.TTS_MODEL
                )
                return b"".join(chunks)
            except Exception as exc:
                last_exc = exc
                log.warning("TTS attempt %d/%d failed: %s", attempt, MAX_RETRIES, exc)
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY_S * attempt)

        raise TTSError(reason=str(last_exc))

    def speak_async(self, text: str) -> None:
        """Fire-and-forget TTS — plays in background thread, never raises."""
        def _run():
            try:
                play(self._convert_with_retry(text))
            except Exception as exc:
                log.error("speak_async failed: %s", exc)
        threading.Thread(target=_run, daemon=True).start()

    async def to_bytes(self, text: str) -> bytes:
        """Convert text → MP3 bytes. Raises TTSError on failure."""
        if not text or not text.strip():
            raise TTSError(reason="Empty text provided.")
        try:
            return self._convert_with_retry(text)
        except TTSError:
            raise
        except Exception as exc:
            raise TTSError(reason=str(exc)) from exc