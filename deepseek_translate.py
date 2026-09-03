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
)


def _get_headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.deepseek_api_key}",
        "Content-Type": "application/json",
    }


async def translate_stream(
    session: aiohttp.ClientSession,
    text: str,
    context: str | None = None,
    hotwords: str = "",
) -> AsyncGenerator[str, None]:
    """Streaming translation: yields tokens as they arrive from DeepSeek.

    context: optional previous translation for coherence.
    hotwords: optional glossary/hotwords to improve accuracy.
    """
    text = (text or "").strip()
    if not text:
        return

    if not settings.deepseek_api_key:
        yield "[DEEPSEEK_API_KEY 未设置]"
        return

    system_content = SYSTEM_PROMPT
    if hotwords:
        system_content += "\n\n专业术语参考（请优先使用这些翻译）：\n" + hotwords

    messages = [{"role": "system", "content": system_content}]
    if context:
        messages.append({
            "role": "system",
            "content": f"Previous sentence translation (for context only): {context}",
        })
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
                yield f"[翻译失败 {resp.status}: {body[:80]}]"
                return

            # DeepSeek SSE format: "data: {json}\n\n"
            async for raw_line in resp.content:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                if line.startswith("data: "):
                    data_str = line[len("data: "):]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices", [])
                    if not choices:
                        continue
                    delta = choices[0].get("delta", {})
                    if "content" in delta:
                        yield delta["content"]
    except Exception as e:
        yield f"[翻译异常: {e}]"
