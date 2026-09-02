"""GET /health — service health check."""

from fastapi import APIRouter

from config import settings

router = APIRouter()


@router.get("/health")
async def health():
    return {
        "status": "ok",
        "asr": settings.asr_model,
        "translation": settings.translation_model,
        "ready": bool(settings.dashscope_api_key and settings.deepseek_api_key),
    }
