"""DeepSeek translation helper. Translates English -> Chinese asynchronously."""

import json
from typing import AsyncGenerator

import aiohttp

from config import settings

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"

SYSTEM_PROMPT = (
    "You are a professional English-to-Chinese translator for live classroom "
    "lectures. Translate the user's English sentence into natural, fluent "
    "Simplified Chinese. Output ONLY the Chinese translation, no quotes, no "
    "explanations, no English. Keep technical terms accurate."
    " Earlier user/assistant pairs are bilingual history for reference only."
    " Translate only the LAST user message. Do not repeat history."
    " If the input is unfinished, translate only what is present; do not invent a continuation."
)


class TranslationError(Exception):
    """API failure, distinct from translated content."""


def _get_headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.deepseek_api_key}",
        "Content-Type": "application/json",
    }


async def translate_stream(
    session: aiohttp.ClientSession,
    text: str,
    context: list[dict] | None = None,
    hotwords: str = "",
) -> AsyncGenerator[str, None]:
    """Streaming translation: yields tokens as they arrive from DeepSeek.

    context: successful preceding English/Chinese pairs for coherence.
    hotwords: optional glossary/hotwords to improve accuracy.
    """
    text = (text or "").strip()
    if not text:
        return

    if not settings.deepseek_api_key:
        raise TranslationError("DEEPSEEK_API_KEY 未设置")

    system_content = SYSTEM_PROMPT
    if hotwords:
        system_content += "\n\n专业术语参考（请优先使用这些翻译）：\n" + hotwords

    messages = [{"role": "system", "content": system_content}]
    for pair in context or []:
        messages.extend([{"role": "user", "content": pair["en"]},
                         {"role": "assistant", "content": pair["zh"]}])
    messages.append({"role": "user", "content": text})

    payload = {
        "model": settings.translation_model,
        "messages": messages,
        "temperature": 0.2,
        "stream": True,
        "max_tokens": 512,
    }
    try:
        async with session.post(
            DEEPSEEK_URL, json=payload, headers=_get_headers(),
            timeout=aiohttp.ClientTimeout(sock_connect=10, sock_read=30),
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise TranslationError(f"翻译失败 {resp.status}: {body[:80]}")

            # DeepSeek SSE format: "data: {json}\n\n"
            completed = False
            async for raw_line in resp.content:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                if line.startswith("data: "):
                    data_str = line[len("data: "):]
                    if data_str.strip() == "[DONE]":
                        completed = True
                        break
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError as exc:
                        raise TranslationError("翻译流返回了无效 JSON") from exc
                    if "error" in chunk:
                        raise TranslationError("翻译流返回错误")
                    choices = chunk.get("choices", [])
                    if not choices:
                        continue
                    delta = choices[0].get("delta", {})
                    if delta.get("content"):
                        yield delta["content"]
            if not completed:
                raise TranslationError("翻译流提前中断")
    except TranslationError:
        raise
    except Exception as e:
        raise TranslationError(f"翻译异常: {e}") from e
