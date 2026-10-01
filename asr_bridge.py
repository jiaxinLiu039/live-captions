"""Serialize ASR callbacks, reconcile segments, and schedule versioned translations."""

import asyncio
import time
from dataclasses import dataclass, field

import aiohttp
from dashscope.audio.asr import RecognitionCallback, RecognitionResult

from config import settings
from session import Session
from protocol import make_v1_result, make_v1_status, make_translation_stream
from deepseek_translate import translate_stream, TranslationError
from segmenter import choose_boundary, remap_cuts, stable_prefix

_EXTRA_BASE = 1 << 30


@dataclass
class SourceSentence:
    order: int = 0
    words: list[str] = field(default_factory=list)
    cuts: list[int] = field(default_factory=list)
    line_ids: list = field(default_factory=list)
    begin_time: float | None = None
    final: bool = False
    timer: asyncio.TimerHandle | None = None


class ASRBridge(RecognitionCallback):
    def __init__(self, session: Session, ws_send_json,
                 loop: asyncio.AbstractEventLoop, http: aiohttp.ClientSession):
        self.session = session
        self._ws_send_json = ws_send_json
        self.loop = loop
        self.http = http
        self._sources: dict = {}
        self._versions: dict = {}
        self._jobs: dict = {}
        self._tasks: set[asyncio.Task] = set()
        self._send_lock = asyncio.Lock()
        self._progressive: dict = {}
        self._extra_seq = 0
        self._source_seq = 0
        self._closed = False
        self._accept_events = True
        self.max_sentence_words = settings.max_sentence_words
        self.progressive_threshold = settings.progressive_threshold
        self.progressive_interval = settings.progressive_interval
        self.progressive_extra_words = settings.progressive_extra_words

    def _spawn(self, coroutine):
        task = self.loop.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def _send(self, payload: dict):
        # SDK callbacks run on a thread; bridge state lives only on the event loop.
        self.loop.call_soon_threadsafe(self._queue_send, payload)

    def _queue_send(self, payload):
        if not self._closed and not self.session.closed:
            self._spawn(self._send_async(payload))

    async def _send_async(self, payload: dict, line_id=None, version=None):
        async with self._send_lock:
            if self._closed or self.session.closed:
                return
            if version is not None and self._versions.get(line_id) != version:
                return
            try:
                await self._ws_send_json(payload)
            except Exception:
                self.session.closed = True

    def _request_translation(self, line_id, text, final=False):
        if self._closed or self.session.closed:
            return
        version = self._versions.get(line_id, 0) + 1
        self._versions[line_id] = version
        previous = self._jobs.get(line_id)
        if previous and not previous.done():
            previous.cancel()
        self.session.begin_translation(line_id, version)
        self._jobs[line_id] = self._spawn(
            self._translate_and_push(line_id, text, version, final))

    async def _translate_and_push(self, line_id, text, version, final):
        def current():
            return (not self._closed and not self.session.closed
                    and self._versions.get(line_id) == version)

        accumulated = ""
        context = self.session.get_translation_context(line_id)
        try:
            await self._send_async(make_translation_stream(
                line_id, "", version=version, state="pending"), line_id, version)
            async for token in translate_stream(self.http, text, context=context,
                                                hotwords=self.session.hotwords):
                if not current():
                    return
                accumulated += token
                await self._send_async(make_translation_stream(
                    line_id, accumulated, version=version), line_id, version)
            if not accumulated.strip():
                raise TranslationError("翻译返回空内容")
            if current() and self.session.update_line_translation(
                    line_id, accumulated, is_final=final, version=version):
                await self._send_async(make_translation_stream(
                    line_id, accumulated, final=final, version=version,
                    state="complete"), line_id, version)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if current():
                self.session.update_line_translation(
                    line_id, accumulated, version=version, state="error")
                await self._send_async({"type": "translation_error", "line_id": line_id,
                                       "version": version, "msg": str(exc)}, line_id, version)

    def on_open(self):
        self._send(make_v1_status("connected"))

    def on_close(self):
        self._send(make_v1_status("closed"))

    def on_error(self, result: RecognitionResult):
        msg = str(result.message or result)
        if "NO_VALID_AUDIO_ERROR" in msg:
            msg = ("未检测到有效音频（NO_VALID_AUDIO_ERROR）。系统音频模式下请确认已勾选"
                   "「分享系统音频」、播放器正在发声，且页面底部波形有跳动。")
        self._send({"type": "error", "code": "asr_failure", "msg": msg, "recoverable": True})

    def on_event(self, result: RecognitionResult):
        sentence = result.get_sentence()
        if sentence is not None:
            self.loop.call_soon_threadsafe(
                self._process_event, dict(sentence), RecognitionResult.is_sentence_end(sentence))

    def _next_extra_id(self):
        self._extra_seq += 1
        return _EXTRA_BASE + self._extra_seq

    def _process_event(self, sentence, final):
        if self._closed or self.session.closed or not self._accept_events:
            return
        sid = sentence.get("sentence_id", "live")
        words = (sentence.get("text") or "").split()
        if sid not in self._sources:
            self._source_seq += 1
            self._sources[sid] = SourceSentence(order=self._source_seq, line_ids=[sid])
        source = self._sources[sid]
        if source.final and not final:
            return
        previous = source.words
        stable = stable_prefix(previous, words)
        source.cuts = remap_cuts(previous, words, source.cuts)
        source.words = words
        if source.begin_time is None:
            try:
                source.begin_time = float(sentence["begin_time"]) / 1000
            except (KeyError, ValueError, TypeError):
                pass
        source.final = final
        self._publish_committed(source)
        if final:
            if source.timer:
                source.timer.cancel()
                source.timer = None
            start = source.cuts[-1] if source.cuts else 0
            if self.max_sentence_words > 0 and len(words) - start > self.max_sentence_words:
                self._split_tail(source, stable=len(words), force=True)
            self._publish_tail(source, final=True)
        else:
            self._split_tail(source, stable=stable)
            self._publish_tail(source)
            self._arm_timer(sid, source)

    def _publish_committed(self, source):
        start = 0
        for index, end in enumerate(source.cuts):
            self._set_line(source.line_ids[index], source.words[start:end], True,
                           source.begin_time, source.order, index)
            start = end

    def _set_line(self, line_id, words, final, begin_time, source_order, segment_index):
        text = " ".join(words)
        existing = self.session.lines.get(line_id)
        changed = existing is None or existing.text != text
        became_final = final and (existing is None or existing.status != "final")
        if not changed and not became_final:
            return
        self.session.update_line_text(line_id, text, final, start_time=begin_time,
                                      source_order=source_order, segment_index=segment_index)
        next_version = self._versions.get(line_id, 0) + (1 if final else 0)
        # Publish source text before starting a fast translation task for that version.
        self._queue_send(make_v1_result(
            line_id, en=text, en_final=final, zh_final=False, start=begin_time,
            order=[source_order, segment_index],
            version=next_version))
        if final:
            self._progressive.pop(line_id, None)
            if text:
                self.session.mark_translated(line_id)
                self._request_translation(line_id, text, final=True)
            else:
                self._clear_line(line_id)

    def _clear_line(self, line_id):
        version = self._versions.get(line_id, 0) + 1
        self._versions[line_id] = version
        task = self._jobs.get(line_id)
        if task and not task.done():
            task.cancel()
        self.session.begin_translation(line_id, version)
        self.session.update_line_translation(line_id, "", True, version=version)
        self._queue_send(make_translation_stream(line_id, "", True, version, "complete"))

    def _split_tail(self, source, stable=0, force=False):
        while True:
            start = source.cuts[-1] if source.cuts else 0
            tail = source.words[start:]
            cut = choose_boundary(tail, self.max_sentence_words,
                                  settings.sentence_split_grace_words,
                                  max(0, stable - start), force)
            if cut is None:
                return
            self._set_line(source.line_ids[-1], tail[:cut], True, source.begin_time,
                           source.order, len(source.cuts))
            source.cuts.append(start + cut)
            source.line_ids.append(self._next_extra_id())
            force = False
            if source.timer:
                source.timer.cancel()
                source.timer = None

    def _publish_tail(self, source, final=False):
        start = source.cuts[-1] if source.cuts else 0
        tail = source.words[start:]
        line_id = source.line_ids[-1]
        if tail or line_id in self.session.lines:
            self._set_line(line_id, tail, final, source.begin_time, source.order, len(source.cuts))
            if not final and tail:
                self._maybe_progressive(line_id, tail)

    def _arm_timer(self, sid, source):
        start = source.cuts[-1] if source.cuts else 0
        if self.max_sentence_words > 0 and len(source.words) - start >= self.max_sentence_words:
            # Keep the original deadline across new callbacks, so continuous speech
            # cannot postpone the time fallback indefinitely.
            if source.timer is None:
                source.timer = self.loop.call_later(settings.sentence_split_wait, self._on_timeout, sid)
        elif source.timer:
            source.timer.cancel()
            source.timer = None

    def _on_timeout(self, sid):
        source = self._sources.get(sid)
        if source is None or source.final or self._closed or self.session.closed:
            return
        source.timer = None
        self._split_tail(source, force=True)
        self._publish_tail(source)
        self._arm_timer(sid, source)

    def _maybe_progressive(self, line_id, words):
        text = " ".join(words)
        now = time.monotonic()
        last_time, last_wc, last_text = self._progressive.get(line_id, (0, 0, ""))
        changed_prefix = last_text and not text.startswith(last_text)
        if (len(words) >= self.progressive_threshold
                and now - last_time >= self.progressive_interval
                and (len(words) > last_wc + self.progressive_extra_words or changed_prefix)):
            self._progressive[line_id] = (now, len(words), text)
            self._request_translation(line_id, text)

    def update_settings(self, **kw):
        for key in ("max_sentence_words", "progressive_threshold", "progressive_interval", "progressive_extra_words"):
            if kw.get(key) is not None:
                value = float(kw[key]) if key == "progressive_interval" else int(kw[key])
                setattr(self, key, max(0.5 if key == "progressive_interval" else 0, value))
        for sid, source in self._sources.items():
            if source.timer:
                source.timer.cancel()
                source.timer = None
            if not source.final:
                self._arm_timer(sid, source)

    async def finish(self, timeout=None):
        """Flush unterminated source tails, then drain current tasks with a deadline."""
        self._accept_events = False
        for source in self._sources.values():
            if source.timer:
                source.timer.cancel()
                source.timer = None
            if not source.final:
                source.final = True
                self._publish_tail(source, final=True)
        deadline = self.loop.time() + (settings.translation_drain_timeout if timeout is None else timeout)
        timed_out = False
        while self._tasks and not self.session.closed:
            remaining = deadline - self.loop.time()
            if remaining <= 0:
                timed_out = True
                break
            await asyncio.wait(tuple(self._tasks), timeout=remaining)
        if timed_out:
            for line_id, task in self._jobs.items():
                if not task.done() and line_id in self.session.lines:
                    self.session.update_line_translation(
                        line_id, self.session.lines[line_id].translation or "",
                        version=self._versions[line_id], state="error")
            # Release any stalled sends before trying to report the timeout itself.
            tasks = tuple(self._tasks)
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            try:
                await asyncio.wait_for(self._send_async({
                    "type": "error", "code": "translation_drain_timeout",
                    "msg": "等待最后译文超时，部分字幕未完成", "recoverable": True}), timeout=0.5)
            except asyncio.TimeoutError:
                self.session.closed = True
        await self.close()
        return not timed_out and not any(
            line.text and not line.translation_final for line in self.session.lines.values())

    async def close(self):
        self._closed = True
        for source in self._sources.values():
            if source.timer:
                source.timer.cancel()
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
