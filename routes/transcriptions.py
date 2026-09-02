"""POST /v1/audio/transcriptions — OpenAI-compatible file transcription."""

import asyncio
import tempfile
from pathlib import Path

import aiohttp
from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse

from config import settings
from deepseek_translate import translate

router = APIRouter()


def _format_srt(segments: list[dict]) -> str:
    """Format segments as SRT subtitle."""
    lines = []
    for i, seg in enumerate(segments, 1):
        start = _seconds_to_srt_time(seg.get("start", 0))
        end = _seconds_to_srt_time(seg.get("end", 0))
        text = seg.get("translation") or seg.get("text", "")
        lines.append(f"{i}\n{start} --> {end}\n{text}\n")
    return "\n".join(lines)


def _format_vtt(segments: list[dict]) -> str:
    """Format segments as WebVTT subtitle."""
    lines = ["WEBVTT\n"]
    for seg in segments:
        start = _seconds_to_vtt_time(seg.get("start", 0))
        end = _seconds_to_vtt_time(seg.get("end", 0))
        text = seg.get("translation") or seg.get("text", "")
        lines.append(f"{start} --> {end}\n{text}\n")
    return "\n".join(lines)


def _seconds_to_srt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _seconds_to_vtt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


async def _transcribe_file(file_path: str) -> list[dict]:
    """Use Paraformer to transcribe an audio file (non-realtime mode)."""
    import dashscope
    from dashscope.audio.asr import Recognition

    dashscope.api_key = settings.dashscope_api_key

    # Use synchronous file recognition in a thread to avoid blocking
    def _run():
        results = []
        recognition = Recognition(
            model="paraformer-v2",
            format="auto",
            sample_rate=16000,
        )
        # For file-based transcription, use the file transcription API
        result = recognition.call(file_path)
        if result and result.output and result.output.get("sentence"):
            for sent in result.output["sentence"]:
                results.append({
                    "text": sent.get("text", ""),
                    "start": sent.get("begin_time", 0) / 1000.0,
                    "end": sent.get("end_time", 0) / 1000.0,
                })
        return results

    return await asyncio.to_thread(_run)


@router.post("/v1/audio/transcriptions")
async def transcribe_audio(
    file: UploadFile = File(...),
    response_format: str = Form(default="json"),
    translate_to_zh: bool = Form(default=True),
):
    """Transcribe an uploaded audio file, optionally translate to Chinese."""
    # File size limit
    content = await file.read()
    max_size = settings.max_upload_mb * 1024 * 1024
    if len(content) > max_size:
        return JSONResponse(
            status_code=413,
            content={"error": f"文件过大，最大允许 {settings.max_upload_mb}MB"},
        )

    # Sanitize suffix (only allow known audio extensions)
    allowed_suffixes = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".webm", ".opus"}
    suffix = Path(file.filename or "audio.wav").suffix.lower()
    if suffix not in allowed_suffixes:
        suffix = ".wav"

    # Save uploaded file temporarily
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        # Transcribe
        segments = await _transcribe_file(tmp_path)

        # Optionally translate
        if translate_to_zh and segments:
            async with aiohttp.ClientSession() as http:
                for seg in segments:
                    if seg["text"].strip():
                        seg["translation"] = await translate(http, seg["text"])
                    else:
                        seg["translation"] = ""

        # Format response
        if response_format == "text":
            text = "\n".join(
                seg.get("translation") or seg["text"] for seg in segments
            )
            return PlainTextResponse(text)
        elif response_format == "srt":
            return PlainTextResponse(_format_srt(segments), media_type="text/plain")
        elif response_format == "vtt":
            return PlainTextResponse(_format_vtt(segments), media_type="text/vtt")
        elif response_format == "verbose_json":
            return JSONResponse({
                "text": " ".join(seg["text"] for seg in segments),
                "segments": segments,
                "language": "en",
            })
        else:  # json
            return JSONResponse({
                "text": " ".join(
                    seg.get("translation") or seg["text"] for seg in segments
                ),
            })
    finally:
        import os
        os.unlink(tmp_path)
