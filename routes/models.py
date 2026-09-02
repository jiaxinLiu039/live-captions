"""GET /v1/models — OpenAI-compatible model listing."""

import time

from fastapi import APIRouter

from config import settings

router = APIRouter()


@router.get("/v1/models")
async def list_models():
    return {
        "object": "list",
        "data": [
            {
                "id": settings.asr_model,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "aliyun-dashscope",
                "type": "asr",
            },
            {
                "id": settings.translation_model,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "deepseek",
                "type": "translation",
            },
        ],
    }
