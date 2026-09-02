"""Per-connection session state management."""

import time
import threading
from dataclasses import dataclass, field

from config import settings
from protocol import new_session_id


@dataclass
class SessionLine:
    """A single finalized or in-progress transcript line."""
    id: int
    text: str = ""
    start: float | None = None
    end: float | None = None
    translation: str | None = None
    status: str = "partial"  # "partial" | "final"
    detected_language: str = "en"


class Session:
    """Manages state for one WebSocket connection. Thread-safe for SDK callbacks."""

    MAX_LINES = 2000  # Evict oldest lines beyond this limit

    def __init__(self, mode: str = "full"):
        self.session_id: str = new_session_id()
        self.mode: str = mode
        self.created_at: float = time.time()

        # Lock protects all mutable state below
        self._lock = threading.Lock()

        # Sequence counter
        self._seq: int = 0

        # Lines storage
        self.lines: dict[int, SessionLine] = {}

        # Translation state
        self.translated: set = set()
        self.last_translations: list[str] = []
        self.translation_context_window: int = 3

        # Stats
        self.audio_bytes: int = 0
        self.translate_count: int = 0

        # Buffers
        self.buffer_transcription: str = ""
        self.buffer_translation: str = ""

        # Hotwords/glossary
        self.hotwords: str = ""

        # Connection state (atomic bool, no lock needed)
        self.closed: bool = False

    def next_seq(self) -> int:
        """Thread-safe sequence number increment."""
        with self._lock:
            self._seq += 1
            return self._seq

    @property
    def audio_seconds(self) -> float:
        with self._lock:
            return self.audio_bytes / (16000 * 2)

    @property
    def session_duration(self) -> float:
        return time.time() - self.created_at

    @property
    def asr_cost(self) -> float:
        """Estimated ASR cost (price/sec configurable via settings.asr_cost_per_sec)."""
        with self._lock:
            return (self.audio_bytes / (16000 * 2)) * settings.asr_cost_per_sec

    @property
    def translate_cost(self) -> float:
        """Estimated translation cost (price/sentence configurable)."""
        with self._lock:
            return self.translate_count * settings.translate_cost_per_sentence

    def add_audio_bytes(self, n: int):
        """Thread-safe audio byte counter."""
        with self._lock:
            self.audio_bytes += n

    def get_or_create_line(self, sentence_id: int) -> SessionLine:
        """Get existing line or create a new one for this sentence_id."""
        with self._lock:
            if sentence_id not in self.lines:
                self.lines[sentence_id] = SessionLine(id=sentence_id)
                self._evict_if_needed()
            return self.lines[sentence_id]

    def _evict_if_needed(self):
        """Remove oldest lines if exceeding MAX_LINES. Must hold lock."""
        if len(self.lines) > self.MAX_LINES:
            keys = sorted(self.lines.keys())
            to_remove = keys[:len(self.lines) - self.MAX_LINES]
            for k in to_remove:
                del self.lines[k]

    def update_line_text(self, sentence_id: int, text: str, is_final: bool, start_time: float | None = None):
        """Update a line's English text from ASR."""
        line = self.get_or_create_line(sentence_id)
        with self._lock:
            line.text = text
            if is_final:
                line.status = "final"
            if start_time is not None:
                line.start = start_time
        return line

    def mark_translated(self, sentence_id: int) -> bool:
        """Mark sentence as queued for translation. Returns True if newly added."""
        with self._lock:
            if sentence_id in self.translated:
                return False
            self.translated.add(sentence_id)
            self.translate_count += 1
            return True

    def update_line_translation(self, sentence_id: int, translation: str, is_final: bool = False):
        """Update a line's Chinese translation."""
        line = self.get_or_create_line(sentence_id)
        with self._lock:
            line.translation = translation
            if is_final:
                self.last_translations.append(translation)
                if len(self.last_translations) > self.translation_context_window:
                    self.last_translations = self.last_translations[-self.translation_context_window:]

    def get_translation_context(self) -> str | None:
        """Get last translation for context-aware translation."""
        with self._lock:
            return self.last_translations[-1] if self.last_translations else None

    def get_all_lines(self) -> list[dict]:
        """Serialize all lines for full-mode transcript message."""
        with self._lock:
            result = []
            for sid in sorted(self.lines.keys()):
                line = self.lines[sid]
                result.append({
                    "id": line.id,
                    "text": line.text,
                    "start": line.start,
                    "end": line.end,
                    "translation": line.translation,
                    "status": line.status,
                    "detected_language": line.detected_language,
                })
            return result

    @property
    def total_lines(self) -> int:
        with self._lock:
            return len(self.lines)
