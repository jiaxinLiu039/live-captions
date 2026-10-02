import asyncio
import json
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from asr_vocabulary import ASRVocabularyCache, VocabularyResult, english_course_terms
from course_profile import CourseProfileStore


class VocabularyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'cache.json'
        self.calls = []
        self.service = self.Service(self.calls)
        self.cache = ASRVocabularyCache(self.path, lambda **kwargs: self.service)
        self.profile = {'name': '最优化理论', 'glossary': 'Hessian = Hessian 矩阵\nKKT conditions = KKT 条件'}

    class Service:
        def __init__(self, calls):
            self.calls = calls
            self.status = 'OK'
        def create_vocabulary(self, **kwargs):
            self.calls.append(('create', kwargs))
            return 'cloud-vocabulary-id'
        def query_vocabulary(self, vocabulary_id):
            self.calls.append(('query', vocabulary_id))
            return {'status': self.status}
        def update_vocabulary(self, vocabulary_id, vocabulary):
            self.calls.append(('update', vocabulary_id, vocabulary))

    async def prepare(self, **kwargs):
        return await self.cache.prepare('course-1', self.profile, 'paraformer-realtime-v2', 'fake-key', **kwargs)

    def test_english_extraction_filters_translations_comments_and_invalid_terms(self):
        words = english_course_terms("# comment\nHessian = 矩阵\nhessian = 重复\n- Newton’s method → 牛顿法\n量子比特\n123\nfirst second third fourth fifth sixth seventh eighth = 太长\nSchrödinger equation = 薛定谔方程")
        self.assertEqual(words, ['Hessian', "Newton's method", 'Schrodinger equation'])
        self.assertEqual(len(english_course_terms('\n'.join('term' + str(i) for i in range(600)))), 500)

    async def test_create_then_reuse_across_process_restart(self):
        first = await self.prepare()
        self.assertEqual((first.vocabulary_id, first.count), ('cloud-vocabulary-id', 2))
        created = [call for call in self.calls if call[0] == 'create']
        self.assertEqual(created[0][1]['target_model'], 'paraformer-realtime-v2')
        self.assertEqual(created[0][1]['vocabulary'][0], {'text': 'Hessian', 'weight': 4, 'lang': 'en'})
        self.cache = ASRVocabularyCache(self.path, lambda **kwargs: self.service)
        self.assertEqual((await self.prepare()).vocabulary_id, first.vocabulary_id)
        self.assertEqual(len([call for call in self.calls if call[0] == 'create']), 1)
        self.assertNotIn('fake-key', self.path.read_text())

    async def test_changed_terms_update_existing_cloud_vocabulary(self):
        await self.prepare()
        self.profile['glossary'] += '\ngradient descent = 梯度下降'
        result = await self.prepare(weight=3)
        self.assertEqual(result.count, 3)
        self.assertEqual(len([call for call in self.calls if call[0] == 'create']), 1)
        self.assertEqual(len([call for call in self.calls if call[0] == 'update']), 1)

    async def test_cache_is_scoped_to_account_model_and_course(self):
        await self.prepare()
        await self.cache.prepare('course-2', self.profile, 'paraformer-realtime-v2', 'fake-key')
        await self.cache.prepare('course-1', self.profile, 'another-model', 'fake-key')
        await self.cache.prepare('course-1', self.profile, 'paraformer-realtime-v2', 'other-key')
        self.assertEqual(len([call for call in self.calls if call[0] == 'create']), 4)

    async def test_failed_compilation_is_visible_and_not_recreated(self):
        self.service.status = 'FAILED'
        result = await self.prepare()
        self.assertIsNone(result.vocabulary_id)
        self.assertIn('加载失败', result.warning)
        await self.prepare()
        self.assertEqual(len([call for call in self.calls if call[0] == 'create']), 1)

    async def test_empty_course_and_missing_key_do_not_call_cloud(self):
        self.assertIsNone((await self.cache.prepare('', {}, 'model', 'fake-key')).vocabulary_id)
        self.assertTrue((await self.cache.prepare('course', self.profile, 'model', '')).warning)
        self.assertEqual(self.calls, [])

    async def test_corrupt_cache_is_preserved(self):
        self.path.write_text('broken cache', encoding='utf-8')
        self.assertTrue((await self.prepare()).warning)
        self.assertEqual(self.path.read_text(), 'broken cache')
        self.assertEqual(self.calls, [])

    async def test_network_failure_falls_back_without_leaking_provider_error(self):
        self.service.create_vocabulary = lambda **kwargs: (_ for _ in ()).throw(RuntimeError('private detail'))
        result = await self.prepare()
        self.assertIsNone(result.vocabulary_id)
        self.assertTrue(result.warning)
        self.assertNotIn('private detail', result.warning)

    async def test_deleted_cloud_vocabulary_is_recreated(self):
        from dashscope.audio.asr.vocabulary import VocabularyServiceException
        await self.prepare()
        query = self.service.query_vocabulary
        deleted = True
        def query_once_deleted(vocabulary_id):
            nonlocal deleted
            if deleted:
                deleted = False
                raise VocabularyServiceException('request', 404, 'NotFound', 'Deleted')
            return query(vocabulary_id)
        self.service.query_vocabulary = query_once_deleted
        self.assertTrue((await self.prepare()).vocabulary_id)
        self.assertEqual(len([call for call in self.calls if call[0] == 'create']), 2)

    async def test_preparation_timeout_returns_visible_fallback(self):
        prepare = self.cache._prepare
        def slow_prepare(*args):
            time.sleep(0.05)
            return prepare(*args)
        self.cache._prepare = slow_prepare
        result = await self.prepare(timeout=0.005)
        self.assertIsNone(result.vocabulary_id)
        self.assertTrue(result.warning)
        # Let the bounded worker finish before cleaning up its temporary cache.
        await asyncio.sleep(0.1)


class ASRStartupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.course_id = str(uuid.uuid4())
        self.store = CourseProfileStore(Path(self.temp.name) / 'courses.json')
        self.store.save(self.course_id, {'name': '最优化理论', 'glossary': 'Hessian = 矩阵'})
        self.store.select('')

    def run_startup(self, vocabulary, fail_start=False):
        from fastapi.testclient import TestClient
        import server
        captured = []
        preparations = []
        async def prepare(course_id, profile, *args, **kwargs):
            preparations.append((course_id, profile))
            return vocabulary
        class Recognition:
            def __init__(self, **kwargs):
                captured.append(kwargs)
            def start(self):
                assert preparations, 'Recognition started before vocabulary preparation'
                if fail_start:
                    raise RuntimeError('start failed')
            def stop(self):
                pass
        with patch.object(server, 'course_profiles', self.store), \
             patch.object(server.asr_vocabularies, 'prepare', prepare), \
             patch.object(server, 'Recognition', Recognition), \
             patch.object(server.settings, 'auth_token', ''):
            with TestClient(server.app) as client:
                with client.websocket_connect('/ws?course_id=' + self.course_id) as ws:
                    config = ws.receive_json()
                    if not fail_start:
                        ws.send_bytes(b'')
                        while ws.receive_json()['type'] != 'ready_to_stop':
                            pass
            self.assertFalse(server.active_sessions)
        return config, captured, preparations

    def test_saved_course_and_english_hint_are_attached_before_asr_start(self):
        config, captured, preparations = self.run_startup(VocabularyResult('cloud-id', 1))
        self.assertEqual(captured[0]['language_hints'], ['en'])
        self.assertEqual(captured[0]['vocabulary_id'], 'cloud-id')
        self.assertEqual(preparations[0][0], self.course_id)
        self.assertEqual(preparations[0][1]['glossary'], 'Hessian = 矩阵')
        self.assertEqual(config['asr']['hotword_count'], 1)
        self.assertEqual(config['asr']['course_name'], '最优化理论')

    def test_hotword_failure_keeps_english_recognition_and_reports_warning(self):
        config, captured, _ = self.run_startup(VocabularyResult(warning='热词加载失败'))
        self.assertEqual(captured[0]['language_hints'], ['en'])
        self.assertNotIn('vocabulary_id', captured[0])
        self.assertFalse(config['asr']['hotwords_enabled'])
        self.assertEqual(config['asr']['warning'], '热词加载失败')

    def test_recognizer_start_failure_cleans_up_session(self):
        config, _, _ = self.run_startup(VocabularyResult(), fail_start=True)
        self.assertEqual(config['type'], 'error')
        self.assertFalse(config['recoverable'])


if __name__ == '__main__':
    unittest.main()
