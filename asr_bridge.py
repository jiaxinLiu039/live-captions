"""ASR Bridge — wraps Aliyun Paraformer SDK callbacks and bridges to async."""

import asyncio
import time

import aiohttp
from dashscope.audio.asr import RecognitionCallback, RecognitionResult

from config import settings
from session import Session
from protocol import (
    make_v1_result,
    make_v1_status,
    make_translation_stream,
)
from deepseek_translate import translate_stream

# Base for synthetic line ids used when a sentence is split into multiple chunks.
# Real Paraformer sentence_ids are small sequential ints; 2^30 keeps us clear.
_EXTRA_BASE = 1 << 30


class ASRBridge(RecognitionCallback):
    """Bridges Paraformer's threaded callbacks to async WebSocket sends."""

    def __init__(
        self,
        session: Session,
        ws_send_json,
        loop: asyncio.AbstractEventLoop,
        http: aiohttp.ClientSession,
    ):
        self.session = session
        self._ws_send_json = ws_send_json  # async callable
        self.loop = loop
        self.http = http
        self._start_time = time.time()
        # Progressive translation tracking: {line_id: (last_translate_time, last_word_count)}
        self._progressive: dict = {}
        # Track which line_id is being translated as "final" to suppress stale progressive output
        self._final_translating: set = set()
        # Sentence-length truncation state (keyed by ASR sentence_id)
        self._consumed: dict = {}   # words already finalized for this ASR sentence
        self._cur_line: dict = {}   # ASR sentence_id -> currently active line_id
        self._extra_seq: int = 0    # counter for synthetic chunk line ids

        # Runtime-tunable settings (adjustable from the UI settings panel)
        self.max_sentence_words = settings.max_sentence_words
        self.progressive_threshold = settings.progressive_threshold
        self.progressive_interval = settings.progressive_interval
        self.progressive_extra_words = settings.progressive_extra_words

    def _send(self, payload: dict):
        """Schedule an async send from a sync (SDK thread) context."""
        if self.session.closed:
            return
        asyncio.run_coroutine_threadsafe(self._send_async(payload), self.loop)

    async def _send_async(self, payload: dict):
        """Send JSON to WebSocket, mark closed on failure."""
        try:
            await self._ws_send_json(payload)
        except Exception:
            self.session.closed = True

    async def _translate_and_push(self, sentence_id, en_text: str, is_final_translate: bool = False):
        """Stream translation from DeepSeek and push tokens to client."""
        context = self.session.get_translation_context()
        accumulated = ""

        try:
            async for token in translate_stream(self.http, en_text, context=context, hotwords=self.session.hotwords):
                # If this is a progressive translate but final has started, stop
                if not is_final_translate and sentence_id in self._final_translating:
                    return
                accumulated += token
                await self._send_async(
                    make_translation_stream(sentence_id, accumulated, final=False)
                )
        except Exception as e:
            error_msg = f"[翻译异常: {e}]"
            await self._send_async(
                make_translation_stream(sentence_id, error_msg, final=True)
            )
            return

        # Final translation
        self.session.update_line_translation(sentence_id, accumulated, is_final=is_final_translate)

        await self._send_async(
            make_translation_stream(sentence_id, accumulated, final=is_final_translate)
        )

        # Cleanup final tracking
        if is_final_translate:
            self._final_translating.discard(sentence_id)

    # --- Paraformer SDK callbacks (called from SDK thread) ---

    def on_open(self) -> None:
        self._send(make_v1_status("connected"))

    def on_close(self) -> None:
        self._send(make_v1_status("closed"))

    def on_error(self, result: RecognitionResult) -> None:
        msg = str(result.message or result)
        # 阿里云原始错误码 → 可操作的中文提示
        if "NO_VALID_AUDIO_ERROR" in msg:
            friendly = (
                "未检测到有效音频（NO_VALID_AUDIO_ERROR）。"
                "系统音频模式下请确认：① 已勾选「分享系统音频」；"
                "② 播放器有声音且在发声；③ 页面底部波形有跳动。"
                "若波形一直平线，请改用 Chrome 或重新开始屏幕共享。"
            )
            self._send({"type": "error", "code": "asr_failure", "msg": friendly, "recoverable": True, "raw": msg})
            return
        self._send({"type": "error", "code": "asr_failure", "msg": msg, "recoverable": True})

    def on_event(self, result: RecognitionResult) -> None:
        sentence = result.get_sentence()
        if sentence is None:
            return

        sid = sentence.get("sentence_id", "live")
        en = sentence.get("text", "") or ""
        is_final = RecognitionResult.is_sentence_end(sentence)
        begin_time = sentence.get("begin_time")

        # Paraformer sends cumulative text per sentence_id. After we finalize a
        # chunk we remember how many words were consumed; the rest belong to a
        # new line. A huge cap (= 0 in config means "off") disables truncation.
        max_words = self.max_sentence_words if self.max_sentence_words > 0 else 10 ** 9

        all_words = en.split()
        consumed = self._consumed.get(sid, 0)
        tail = all_words[consumed:]

        while tail:
            line_id = self._cur_line.get(sid)
            is_new_line = line_id is None
            if is_new_line:
                line_id = sid if consumed == 0 else self._next_extra_id()
                self._cur_line[sid] = line_id
            start_seconds = self._rel_seconds(begin_time) if is_new_line else None

            if len(tail) > max_words:
                # Sentence too long: finalize a chunk and keep the rest for a new line
                chunk, tail = tail[:max_words], tail[max_words:]
                self._finalize_chunk(line_id, chunk, start_seconds)
                consumed += len(chunk)
                self._consumed[sid] = consumed
                self._cur_line.pop(sid, None)
            else:
                # Remaining words fit: show them as the live (or final) line
                self._update_live(line_id, tail, is_final, start_seconds)
                if is_final:
                    consumed += len(tail)
                    self._consumed[sid] = consumed
                    self._cur_line.pop(sid, None)
                    self._progressive.pop(line_id, None)
                    if tail:
                        self._final_translating.add(line_id)
                        if self.session.mark_translated(line_id):
                            asyncio.run_coroutine_threadsafe(
                                self._translate_and_push(line_id, " ".join(tail), is_final_translate=True), self.loop
                            )
                else:
                    self._maybe_progressive(line_id, tail)
                break

    # --- Helpers for sentence-length truncation ---

    def _rel_seconds(self, begin_time):
        """Convert begin_time (ms, may be str) to seconds, or None."""
        if begin_time is None:
            return None
        try:
            return float(begin_time) / 1000.0
        except (TypeError, ValueError):
            return None

    def _next_extra_id(self) -> int:
        """Allocate a synthetic line id for a continuation chunk."""
        self._extra_seq += 1
        return _EXTRA_BASE + self._extra_seq

    def _finalize_chunk(self, line_id, words, start_seconds):
        """Treat a word chunk as a completed sentence: update state, send + translate."""
        text = " ".join(words)
        self.session.update_line_text(line_id, text, True, start_time=start_seconds)
        self._send(make_v1_result(line_id, en=text, en_final=True))
        self._progressive.pop(line_id, None)
        self._final_translating.add(line_id)
        if self.session.mark_translated(line_id):
            asyncio.run_coroutine_threadsafe(
                self._translate_and_push(line_id, text, is_final_translate=True), self.loop
            )

    def _update_live(self, line_id, words, is_final, start_seconds):
        """Update the client's view of a live line with the current tail words."""
        text = " ".join(words)
        self.session.update_line_text(line_id, text, is_final, start_time=start_seconds)
        self._send(make_v1_result(line_id, en=text, en_final=is_final))

    def _maybe_progressive(self, line_id, words):
        """Progressive translation for a still-growing line, rate-limited."""
        text = " ".join(words)
        if not text.strip():
            return
        word_count = len(words)
        now = time.time()
        last_time, last_wc = self._progressive.get(line_id, (0, 0))

        # Trigger if: enough new words AND enough time since last progressive translate
        if (word_count >= self.progressive_threshold
                and (now - last_time) >= self.progressive_interval
                and word_count > last_wc + self.progressive_extra_words):
            self._progressive[line_id] = (now, word_count)
            asyncio.run_coroutine_threadsafe(
                self._translate_and_push(line_id, text), self.loop
            )

    def update_settings(self, **kw):
        """Apply runtime-tunable settings sent from the frontend settings panel."""
        for key in ("max_sentence_words", "progressive_threshold", "progressive_interval", "progressive_extra_words"):
            if key in kw and kw[key] is not None:
                val = kw[key]
                if key == "progressive_interval":
                    setattr(self, key, max(0.5, float(val)))
                else:
                    setattr(self, key, max(0, int(val)))
