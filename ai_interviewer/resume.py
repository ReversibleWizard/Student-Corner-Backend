import os
from pypdf import PdfReader

from ai_interviewer.exceptions import ResumeLoadError
from ai_interviewer.logger import get_logger

log = get_logger(__name__)

MAX_RESUME_PAGES = 10


class ResumeLoader:
    """Loads and parses a PDF resume into plain text."""

    def __init__(self, path: str = "user/resume.pdf"):
        self.path = path

    def load(self) -> str:
        """
        Extract text from PDF.
        Returns empty string if file missing (non-fatal).
        Raises ResumeLoadError if file exists but cannot be parsed.
        """
        if not os.path.exists(self.path):
            log.warning("Resume not found at '%s' — continuing without it.", self.path)
            return ""

        try:
            reader = PdfReader(self.path)

            if len(reader.pages) > MAX_RESUME_PAGES:
                log.warning("Resume has %d pages — truncating to %d.", len(reader.pages), MAX_RESUME_PAGES)

            text = ""
            for page in reader.pages[:MAX_RESUME_PAGES]:
                extracted = page.extract_text()
                if extracted:
                    text += extracted

            if not text.strip():
                log.warning("Resume at '%s' yielded no extractable text.", self.path)
                return ""

            log.info("Resume loaded: %d chars from '%s'.", len(text), self.path)
            return text

        except Exception as exc:
            log.error("Failed to load resume from '%s': %s", self.path, exc)
            raise ResumeLoadError(path=self.path, reason=str(exc)) from exc