"""Protocol message constructors for WebSocket communication (V1 + V2)."""

import time
import uuid
from typing import Any


def make_config(session_id: str, mode: str = "full", stop_timeout_ms: int = 17000) -> dict:
    """Config message sent immediately after WebSocket accept."""
    return {
        "type": "config",
        "version": "2.0",
        "session_id": session_id,
        "mode": mode,
        "stop_timeout_ms": stop_timeout_ms,
        "capabilities": {
            "asr": "paraformer-realtime-v2",
            "translation": "deepseek-chat",
            "supported_modes": ["full", "diff"],
            "supported_languages": ["en"],
            "translation_targets": ["zh"],
        },
    }


def make_status(state: str, msg: str = "", **extra) -> dict:
    """Status/lifecycle message."""
    payload = {"type": "status", "state": state, "msg": msg}
    payload.update(extra)
    return payload


def make_error(code: str, msg: str, recoverable: bool = True) -> dict:
    """Error message."""
    return {
        "type": "error",
        "code": code,
        "msg": msg,
        "recoverable": recoverable,
    }


def make_transcript_full(lines: list[dict], seq: int, **buffers) -> dict:
    """Full-mode transcript update (all lines)."""
    payload = {
        "type": "transcript",
        "mode": "full",
        "seq": seq,
        "lines": lines,
        "timestamp": time.time(),
    }
    payload.update(buffers)
    return payload


def make_transcript_diff(
    new_lines: list[dict],
    updated_lines: list[dict],
    seq: int,
    **buffers,
) -> dict:
    """Diff-mode transcript update (only new/changed lines)."""
    payload = {
        "type": "transcript",
        "mode": "diff",
        "seq": seq,
        "new_lines": new_lines,
        "updated_lines": updated_lines,
        "timestamp": time.time(),
    }
    payload.update(buffers)
    return payload


def make_translation_stream(line_id: Any, accumulated: str, final: bool = False,
                            version: int = 0, state: str = "streaming") -> dict:
    """Streaming translation token message."""
    return {
        "type": "translation_stream",
        "line_id": line_id,
        "accumulated": accumulated,
        "final": final,
        "version": version,
        "state": state,
    }


def make_stats(
    audio_seconds: float,
    translate_count: int,
    asr_cost: float,
    translate_cost: float,
    session_duration: float = 0,
) -> dict:
    """Periodic stats message."""
    total_cost = asr_cost + translate_cost
    return {
        "type": "stats",
        "audio_seconds": round(audio_seconds, 1),
        "translate_count": translate_count,
        "asr_cost": round(asr_cost, 4),
        "translate_cost": round(translate_cost, 4),
        "total_cost": round(total_cost, 4),
        "session_duration": round(session_duration, 1),
    }


def make_ready_to_stop(total_lines: int, complete: bool = True) -> dict:
    """End-of-audio acknowledgment."""
    return {"type": "ready_to_stop", "total_lines": total_lines, "complete": complete}


# --- V1 compatibility (current frontend) ---

def make_v1_result(sentence_id: Any, **fields) -> dict:
    """Legacy V1 result message for backwards compatibility."""
    payload = {"type": "result", "sentence_id": sentence_id}
    payload.update(fields)
    return payload


def make_v1_status(msg: str) -> dict:
    """Legacy V1 status message."""
    return {"type": "status", "msg": msg}


def new_session_id() -> str:
    """Generate a unique session ID."""
    return str(uuid.uuid4())
