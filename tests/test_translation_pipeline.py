"""Deterministic regressions; no real ASR/translation API calls."""

import asyncio
import json
import random
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asr_bridge
import deepseek_translate
from asr_bridge import ASRBridge
from config import settings
from deepseek_translate import TranslationError
from segmenter import boundary_strength, choose_boundary, remap_cuts
from session import Session


class SegmentationTests(unittest.TestCase):
    def test_abbreviations_decimals_and_punctuation(self):
        for word in ('Dr.', 'e.g.', 'U.S.', 'A.', '3.14', '3.14.'):
            self.assertEqual(boundary_strength(word), 0, word)
        self.assertEqual(boundary_strength('finished."'), 2)
        self.assertEqual(boundary_strength('however,'), 1)

    def test_stable_punctuation_preferred_over_fixed_count(self):
        words = 'This is a complete sentence. Another clause has more words'.split()
        self.assertEqual(choose_boundary(words, 6, stable=len(words)), 5)
        self.assertIsNone(choose_boundary(words, 6, stable=2))

    def test_wait_hard_cap_disabled_and_negation(self):
        words = 'The treatment did not significantly improve survival today'.split()
        self.assertIsNone(choose_boundary(words, 4, grace=10))
        self.assertEqual(choose_boundary(words, 4, force=True), 3)
        self.assertEqual(choose_boundary(words, 4, grace=2), 3)
        self.assertIsNone(choose_boundary(words, 0, force=True))

    def test_remap_insertions_and_deletions(self):
        old = 'We see results. It works well'.split()
        new = 'Now we see better results. It works well'.split()
        self.assertEqual(remap_cuts(old, new, [3]), [5])
        self.assertEqual(remap_cuts(old, old[3:], [3]), [0])


class ContextTests(unittest.TestCase):
    def make_line(self, session, sid, text, zh, final=True, state='complete'):
        session.update_line_text(sid, text, True)
        session.begin_translation(sid, 1)
        session.update_line_translation(sid, zh, final, version=1, state=state)

    def test_source_order_not_numeric_id_or_completion_order(self):
        session = Session()
        for sid in (1, 1 << 30, 2, 3):
            session.update_line_text(sid, str(sid), True)
            session.begin_translation(sid, 1)
        for sid in (2, 1 << 30, 1):
            session.update_line_translation(sid, '中' + str(sid), True, version=1)
        self.assertEqual([p['en'] for p in session.get_translation_context(3)],
                         ['1', str(1 << 30), '2'])
        self.assertEqual([line['id'] for line in session.get_all_lines()], [1, 1 << 30, 2, 3])
        self.assertEqual(session.get_translation_context(1), [])

    def test_excludes_drafts_errors_current_and_future(self):
        session = Session()
        self.make_line(session, 1, 'draft', '草稿', final=False)
        self.make_line(session, 2, 'bad', '部分', state='error')
        self.make_line(session, 3, 'good', '正确')
        self.make_line(session, 4, 'current', '当前')
        self.make_line(session, 5, 'future', '未来')
        self.assertEqual(session.get_translation_context(4), [{'en': 'good', 'zh': '正确'}])
        session.update_line_text(3, 'corrected', True)
        self.assertEqual(session.get_translation_context(4), [])

    def test_budget_disabled_and_stale_write(self):
        session = Session()
        self.make_line(session, 1, 'English', '中文')
        session.update_line_text(2, 'current', True)
        with patch.object(settings, 'translation_context_chars', 2):
            self.assertEqual(session.get_translation_context(2), [])
        session.translation_context_window = 0
        self.assertEqual(session.get_translation_context(2), [])
        session.begin_translation(1, 2)
        self.assertFalse(session.update_line_translation(1, '旧', True, version=1))
        self.assertEqual(session.lines[1].translation, '中文')

    def test_evicted_line_is_not_resurrected_by_a_late_response(self):
        session = Session()
        session.MAX_LINES = 1
        self.make_line(session, 1, 'old', '旧')
        session.update_line_text(2, 'new', True)
        self.assertFalse(session.update_line_translation(1, '晚到', True, version=1))
        self.assertEqual(list(session.lines), [2])


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.messages = []
        self.calls = []
        self.session = Session()
        async def send(message):
            self.messages.append(message)
        self.bridge = ASRBridge(self.session, send, asyncio.get_running_loop(), None)
        self.bridge.progressive_threshold = 1000
        async def translate(http, text, **kwargs):
            self.calls.append((text, kwargs.get('context')))
            yield '译:' + text
        self.mock = patch.object(asr_bridge, 'translate_stream', translate)
        self.mock.start()

    async def asyncTearDown(self):
        await self.bridge.close()
        self.mock.stop()

    def event(self, text, final=False, sid=0):
        self.bridge._process_event({'sentence_id': sid, 'text': text, 'begin_time': 1000}, final)

    async def settle(self):
        for _ in range(5):
            await asyncio.sleep(0)

    async def test_old_request_ignoring_cancel_cannot_overwrite_final(self):
        entered = asyncio.Event()
        release = asyncio.Event()
        async def translate(http, text, **kwargs):
            if text == 'old':
                entered.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    await release.wait()  # emulate a late result despite cancellation
                yield '旧'
            else:
                yield '新'
        with patch.object(asr_bridge, 'translate_stream', translate):
            self.session.update_line_text(0, 'old', False)
            self.bridge._request_translation(0, 'old')
            await entered.wait()
            self.session.update_line_text(0, 'new', True)
            self.bridge._request_translation(0, 'new', final=True)
            await self.settle()
            release.set()
            await self.settle()
        self.assertEqual(self.session.lines[0].translation, '新')
        self.assertTrue(self.session.lines[0].translation_final)
        self.assertFalse(any(m.get('accumulated') == '旧' for m in self.messages))

    async def test_result_precedes_fast_final_and_duplicate_is_not_retranslated(self):
        self.event('A complete sentence.', True)
        await self.settle()
        self.event('A complete sentence.', True)
        await self.settle()
        self.assertEqual(len(self.calls), 1)
        kinds = [m['type'] for m in self.messages]
        self.assertLess(kinds.index('result'), kinds.index('translation_stream'))
        self.assertTrue(self.session.lines[0].translation_final)

    async def test_final_correction_after_split_preserves_all_words(self):
        self.bridge.max_sentence_words = 5
        old = 'We see clear results. It works really well today'
        self.event(old)
        self.event(old)
        await self.settle()
        final = 'Now we see better results. It works very well today'
        self.event(final, True)
        await self.settle()
        actual = ' '.join(line.text for line in self.session.lines.values() if line.text)
        self.assertEqual(actual, final)
        self.assertTrue(all(line.translation_final for line in self.session.lines.values()))

    async def test_late_split_of_earlier_source_keeps_chronological_context(self):
        self.bridge.max_sentence_words = 5
        self.event('The earlier source starts', sid=0)
        self.event('A later sentence.', True, sid=1)
        await self.settle()
        self.event('The earlier source starts and now continues for several words', True, sid=0)
        await self.settle()
        source = self.bridge._sources[0]
        tail_id = source.line_ids[-1]
        context = self.session.get_translation_context(tail_id)
        self.assertFalse(any(pair['en'] == 'A later sentence.' for pair in context))
        ordered_ids = [line['id'] for line in self.session.get_all_lines()]
        self.assertLess(ordered_ids.index(tail_id), ordered_ids.index(1))

    async def test_deleted_committed_range_clears_old_translation(self):
        self.bridge.max_sentence_words = 5
        text = 'This was the introduction. Now we start the actual lesson'
        self.event(text)
        self.event(text)
        await self.settle()
        self.event('Now we start the actual lesson', True)
        await self.settle()
        self.assertEqual(self.session.lines[0].text, '')
        self.assertEqual(self.session.lines[0].translation, '')
        actual = ' '.join(line.text for line in self.session.lines.values() if line.text)
        self.assertEqual(actual, 'Now we start the actual lesson')

    async def test_timer_fires_without_new_callback_and_does_not_reset(self):
        self.bridge.max_sentence_words = 5
        with patch.object(settings, 'sentence_split_wait', 0.02):
            self.event('one two three four five six')
            first = self.bridge._sources[0].timer
            self.event('one two three four five six seven')
            self.assertIs(self.bridge._sources[0].timer, first)
            await asyncio.sleep(0.04)
        self.assertTrue(self.bridge._sources[0].cuts)
        self.assertEqual(self.session.lines[0].text, 'one two three four five')

    async def test_disabled_splitting_and_final_flush(self):
        self.bridge.max_sentence_words = 0
        self.event(' '.join(['word'] * 50))
        self.assertIsNone(self.bridge._sources[0].timer)
        self.assertTrue(await self.bridge.finish(timeout=0.2))
        self.assertEqual(len(self.session.lines), 1)
        self.assertTrue(self.session.lines[0].translation_final)

    async def test_error_is_separate_and_finish_reports_incomplete(self):
        async def fail(http, text, **kwargs):
            yield '部分内容'
            raise TranslationError('测试错误')
        with patch.object(asr_bridge, 'translate_stream', fail):
            self.event('A sentence.', True)
            self.assertFalse(await self.bridge.finish(timeout=0.2))
        self.assertEqual(self.session.lines[0].translation, '部分内容')
        self.assertFalse(self.session.lines[0].translation_final)
        self.assertTrue(any(m['type'] == 'translation_error' for m in self.messages))

    async def test_finish_deadline_cancels_jobs_and_ignores_late_callbacks(self):
        async def slow(http, text, **kwargs):
            await asyncio.sleep(5)
            yield 'too late'
        with patch.object(asr_bridge, 'translate_stream', slow):
            self.event('An unfinished sentence')
            self.assertFalse(await self.bridge.finish(timeout=0.01))
        count = len(self.messages)
        self.event('Late callback', True)
        await self.settle()
        self.assertEqual(len(self.messages), count)
        self.assertFalse(self.session.lines[0].translation_final)

    async def test_stalled_send_does_not_hold_finish_forever(self):
        async def stalled(message):
            await asyncio.sleep(10)
        self.bridge._ws_send_json = stalled
        self.event('A last sentence.', True)
        self.assertFalse(await asyncio.wait_for(self.bridge.finish(timeout=0.01), timeout=1))

    async def test_partial_correction_with_same_word_count_triggers_translation(self):
        self.bridge.max_sentence_words = 0
        self.bridge.progressive_threshold = 1
        self.bridge.progressive_extra_words = 0
        self.bridge.progressive_interval = 0
        self.event('The result is significant')
        await self.settle()
        self.event('The result is insignificant')
        await self.settle()
        self.assertEqual(len(self.calls), 2)

    async def test_random_asr_edits_do_not_drop_or_duplicate_text(self):
        rng = random.Random(7)
        self.bridge.max_sentence_words = 5
        words = 'a b c d e f g h i j k l m n o p q r s t'.split()
        for _ in range(30):
            index = rng.randrange(len(words))
            if rng.random() < 0.5:
                words.insert(index, 'inserted')
            else:
                words.pop(index)
            self.event(' '.join(words))
        self.event(' '.join(words), True)
        await self.settle()
        source = self.bridge._sources[0]
        actual = ' '.join(self.session.lines[sid].text for sid in source.line_ids
                          if sid in self.session.lines and self.session.lines[sid].text)
        self.assertEqual(actual, ' '.join(words))


class TranslationAPITests(unittest.IsolatedAsyncioTestCase):
    class Response:
        status = 200
        def __init__(self, lines):
            self.lines = lines
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        @property
        def content(self):
            async def stream():
                for line in self.lines:
                    yield line
            return stream()
        async def text(self):
            return 'provider failed'

    class HTTP:
        def __init__(self, response):
            self.response = response
            self.payload = None
        def post(self, url, **kwargs):
            self.payload = kwargs['json']
            return self.response

    async def test_bilingual_payload_and_successful_stream(self):
        data = json.dumps({'choices': [{'delta': {'content': '译文'}}]}, ensure_ascii=False)
        http = self.HTTP(self.Response([('data: ' + data + '\n').encode(), b'data: [DONE]\n']))
        with patch.object(settings, 'deepseek_api_key', 'test-key'):
            tokens = [token async for token in deepseek_translate.translate_stream(
                http, 'Current', context=[{'en': 'Previous', 'zh': '前文'}], hotwords='term = 术语')]
        self.assertEqual(tokens, ['译文'])
        messages = http.payload['messages']
        self.assertEqual([m['role'] for m in messages], ['system', 'user', 'assistant', 'user'])
        self.assertEqual(messages[-1]['content'], 'Current')
        self.assertIn('term = 术语', messages[0]['content'])

    async def test_missing_key_http_error_and_truncated_stream_raise(self):
        with patch.object(settings, 'deepseek_api_key', ''):
            with self.assertRaises(TranslationError):
                _ = [token async for token in deepseek_translate.translate_stream(None, 'text')]
        for status in (200, 429):
            response = self.Response([])
            response.status = status
            with patch.object(settings, 'deepseek_api_key', 'test-key'):
                with self.assertRaises(TranslationError):
                    _ = [token async for token in deepseek_translate.translate_stream(self.HTTP(response), 'text')]


class WebSocketTests(unittest.TestCase):
    def test_end_of_audio_delivers_final_translation_before_ready(self):
        from fastapi.testclient import TestClient
        import server
        class Recognition:
            def __init__(self, **kwargs):
                self.callback = kwargs['callback']
            def start(self):
                pass
            def send_audio_frame(self, data):
                pass
            def stop(self):
                self.callback.loop.call_soon_threadsafe(self.callback._process_event,
                    {'sentence_id': 0, 'text': 'Last sentence.', 'begin_time': 0}, True)
        async def translate(http, text, **kwargs):
            await asyncio.sleep(0.03)
            yield '最后一句。'
        with patch.object(server, 'Recognition', Recognition), \
             patch.object(asr_bridge, 'translate_stream', translate), \
             patch.object(settings, 'auth_token', ''):
            with TestClient(server.app) as client:
                with client.websocket_connect('/ws') as ws:
                    self.assertEqual(ws.receive_json()['type'], 'config')
                    ws.send_bytes(b'')
                    messages = []
                    while True:
                        message = ws.receive_json()
                        messages.append(message)
                        if message['type'] == 'ready_to_stop':
                            break
                    self.assertTrue(messages[-1]['complete'])
                    self.assertTrue(any(m.get('final') and m.get('accumulated') == '最后一句。' for m in messages))
            self.assertFalse(server.active_sessions)

    def test_provider_failure_is_reported_as_incomplete_stop(self):
        from fastapi.testclient import TestClient
        import server
        class Recognition:
            def __init__(self, **kwargs):
                self.callback = kwargs['callback']
            def start(self):
                pass
            def stop(self):
                self.callback.loop.call_soon_threadsafe(self.callback._process_event,
                    {'sentence_id': 0, 'text': 'Last sentence.'}, True)
        async def fail(http, text, **kwargs):
            raise TranslationError('provider failed')
            yield  # make this a streaming async generator
        with patch.object(server, 'Recognition', Recognition), \
             patch.object(asr_bridge, 'translate_stream', fail), \
             patch.object(settings, 'auth_token', ''):
            with TestClient(server.app) as client:
                with client.websocket_connect('/ws') as ws:
                    ws.receive_json()
                    ws.send_bytes(b'')
                    messages = []
                    while True:
                        message = ws.receive_json()
                        messages.append(message)
                        if message['type'] == 'ready_to_stop':
                            break
                    self.assertFalse(messages[-1]['complete'])
                    self.assertTrue(any(m['type'] == 'translation_error' for m in messages))


if __name__ == '__main__':
    unittest.main()
