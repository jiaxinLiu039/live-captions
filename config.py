"""Application configuration — loads .env and provides typed settings."""

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent


def _env_int(key: str, default: int) -> int:
    """Parse an int from env, falling back to default on missing/invalid."""
    try:
        return int(os.getenv(key, ""))
    except (TypeError, ValueError):
        return default


def _env_float(key: str, default: float) -> float:
    """Parse a float from env, falling back to default on missing/invalid."""
    try:
        return float(os.getenv(key, ""))
    except (TypeError, ValueError):
        return default

load_dotenv(BASE_DIR / ".env")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("translater")


@dataclass
class Settings:
    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    auth_token: str = field(default_factory=lambda: os.getenv("AUTH_TOKEN", ""))  # empty = no auth
    max_connections: int = 10  # max concurrent WebSocket sessions

    # ASR (Aliyun Paraformer)
    dashscope_api_key: str = field(default_factory=lambda: os.getenv("DASHSCOPE_API_KEY", ""))
    asr_model: str = "paraformer-realtime-v2"
    asr_format: str = "pcm"
    asr_sample_rate: int = 16000
    asr_hotword_weight: int = field(default_factory=lambda: min(5, max(1, _env_int("ASR_HOTWORD_WEIGHT", 4))))
    asr_hotword_timeout: float = field(default_factory=lambda: max(1.0, _env_float("ASR_HOTWORD_TIMEOUT", 15.0)))

    # Translation (DeepSeek)
    deepseek_api_key: str = field(default_factory=lambda: os.getenv("DEEPSEEK_API_KEY", ""))
    translation_model: str = "deepseek-chat"
    translation_context_window: int = field(default_factory=lambda: max(0, _env_int("TRANSLATION_CONTEXT_WINDOW", 3)))
    translation_context_chars: int = field(default_factory=lambda: max(0, _env_int("TRANSLATION_CONTEXT_CHARS", 3000)))
    translation_drain_timeout: float = field(default_factory=lambda: max(0.5, _env_float("TRANSLATION_DRAIN_TIMEOUT", 10.0)))

    # Sentence length cap: live sentences longer than this many words are split
    # into chunks (each finalized + translated once). Set to 0 to disable.
    max_sentence_words: int = field(default_factory=lambda: _env_int("MAX_SENTENCE_WORDS", 25))
    sentence_split_grace_words: int = field(default_factory=lambda: max(0, _env_int("SENTENCE_SPLIT_GRACE_WORDS", 10)))
    sentence_split_wait: float = field(default_factory=lambda: max(0.5, _env_float("SENTENCE_SPLIT_WAIT", 3.0)))

    # Progressive translation tuning — how often a still-growing partial sentence
    # gets an early translation (adjustable from the UI settings panel)
    progressive_threshold: int = field(default_factory=lambda: _env_int("PROGRESSIVE_THRESHOLD", 12))
    progressive_interval: float = field(default_factory=lambda: _env_float("PROGRESSIVE_INTERVAL", 2.0))
    progressive_extra_words: int = field(default_factory=lambda: _env_int("PROGRESSIVE_EXTRA_WORDS", 4))

    # Cost estimation (configurable unit prices)
    asr_cost_per_sec: float = field(default_factory=lambda: _env_float("ASR_COST_PER_SEC", 0.00024))
    translate_cost_per_sentence: float = field(default_factory=lambda: _env_float("TRANSLATE_COST_PER_SENTENCE", 0.00002))

    # Protocol
    stats_interval: float = 5.0  # seconds between stats pushes

    def validate(self):
        """Log warnings for missing keys."""
        if not self.dashscope_api_key:
            logger.warning("DASHSCOPE_API_KEY not set in .env")
        if not self.deepseek_api_key:
            logger.warning("DEEPSEEK_API_KEY not set in .env")
        if self.auth_token:
            logger.info("Token authentication enabled")
        else:
            logger.warning("No AUTH_TOKEN set — WebSocket endpoint is unprotected")


settings = Settings()
settings.validate()
