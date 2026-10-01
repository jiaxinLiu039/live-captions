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
    order: int = 0
    segment_index: int = 0
    translation_version: int = 0
    translation_final: bool = False
    translation_state: str = "idle"


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
        self.translation_context_window = settings.translation_context_window
        self._line_order = 0

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
                self._line_order += 1
                self.lines[sentence_id] = SessionLine(id=sentence_id, order=self._line_order)
                self._evict_if_needed()
            return self.lines[sentence_id]

    def _evict_if_needed(self):
        """Remove oldest lines if exceeding MAX_LINES. Must hold lock."""
        if len(self.lines) > self.MAX_LINES:
            keys = sorted(self.lines, key=lambda key: (self.lines[key].order, self.lines[key].segment_index))
            to_remove = keys[:len(self.lines) - self.MAX_LINES]
            for k in to_remove:
                del self.lines[k]

    def update_line_text(self, sentence_id: int, text: str, is_final: bool,
                         start_time: float | None = None, source_order: int | None = None,
                         segment_index: int = 0):
        """Update a line's English text from ASR."""
        line = self.get_or_create_line(sentence_id)
        with self._lock:
            if line.text != text:
                line.translation_final = False
            line.text = text
            if source_order is not None:
                line.order = source_order
                line.segment_index = segment_index
            line.status = "final" if is_final else "partial"
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

    def begin_translation(self, sentence_id: int, version: int):
        line = self.get_or_create_line(sentence_id)
        with self._lock:
            line.translation_version = version
            line.translation_final = False
            line.translation_state = "pending"

    def update_line_translation(self, sentence_id: int, translation: str,
                                is_final: bool = False, version: int | None = None,
                                state: str = "complete") -> bool:
        """Update a line's Chinese translation."""
        if version is None:
            self.get_or_create_line(sentence_id)
        with self._lock:
            line = self.lines.get(sentence_id)
            if line is None:
                return False
            if version is not None and version != line.translation_version:
                return False
            line.translation = translation
            line.translation_final = is_final and state == "complete"
            line.translation_state = state
            return True

    def get_translation_context(self, sentence_id: int) -> list[dict]:
        """Snapshot successful preceding bilingual lines in source order."""
        with self._lock:
            current = self.lines.get(sentence_id)
            if current is None or self.translation_context_window <= 0:
                return []
            preceding = sorted((line for line in self.lines.values()
                                if (line.order, line.segment_index) < (current.order, current.segment_index)
                                and line.translation_final
                                and line.status == "final" and line.text and line.translation),
                               key=lambda line: (line.order, line.segment_index))
            budget = settings.translation_context_chars
            history = []
            for line in reversed(preceding[-self.translation_context_window:]):
                size = len(line.text) + len(line.translation)
                if size > budget:
                    break
                history.append({"en": line.text, "zh": line.translation})
                budget -= size
            return list(reversed(history))

    def get_all_lines(self) -> list[dict]:
        """Serialize all lines for full-mode transcript message."""
        with self._lock:
            result = []
            for sid in sorted(self.lines, key=lambda key: (self.lines[key].order, self.lines[key].segment_index)):
                line = self.lines[sid]
                result.append({
                    "id": line.id,
                    "text": line.text,
                    "start": line.start,
                    "end": line.end,
                    "translation": line.translation,
                    "status": line.status,
                    "order": [line.order, line.segment_index],
                    "translation_version": line.translation_version,
                    "translation_final": line.translation_final,
                    "translation_state": line.translation_state,
                    "detected_language": line.detected_language,
                })
            return result

    @property
    def total_lines(self) -> int:
        with self._lock:
            return len(self.lines)
