import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from course_profile import CourseProfileStore, normalize_course_profile


class CourseProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'course_profiles.json'
        self.store = CourseProfileStore(self.path)
        self.first = str(uuid.uuid4())
        self.second = str(uuid.uuid4())

    def test_save_switch_update_delete_and_restart(self):
        self.assertEqual(self.store.read(), {'profiles': [], 'activeId': ''})
        self.store.save(self.first, {'name': '机器学习', 'topic': '梯度下降'})
        self.store.save(self.second, {'name': '统计学', 'glossary': 'significant = 显著'})
        self.store.select(self.first)
        reopened = CourseProfileStore(self.path)
        self.assertEqual(reopened.read()['activeId'], self.first)
        self.assertEqual(len(reopened.read()['profiles']), 2)
        reopened.save(self.first, {'name': '机器学习', 'topic': '反向传播'})
        self.assertEqual(len(reopened.read()['profiles']), 2)
        reopened.delete(self.first)
        self.assertEqual(reopened.read()['activeId'], '')
        self.assertEqual(reopened.read()['profiles'][0]['name'], '统计学')
        reopened.select('')
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['activeId'], '')

    def test_presets_initialize_new_project_and_do_not_restore_deleted_courses(self):
        presets = Path(self.temp.name) / 'presets.json'
        presets.write_text(json.dumps({'profiles': [{'id': self.first, 'name': '最优化理论'}],
                                      'activeId': ''}, ensure_ascii=False), encoding='utf-8')
        store = CourseProfileStore(self.path, presets)
        self.assertEqual(store.read()['profiles'][0]['name'], '最优化理论')
        store.delete(self.first)
        self.assertEqual(CourseProfileStore(self.path, presets).read()['profiles'], [])
        self.assertEqual(json.loads(presets.read_text(encoding='utf-8'))['profiles'][0]['name'], '最优化理论')

    def test_corrupt_file_is_not_overwritten(self):
        self.path.write_text('invalid JSON', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.store.save(self.first, {'name': '课程'})
        self.assertEqual(self.path.read_text(encoding='utf-8'), 'invalid JSON')

    def test_failed_atomic_write_preserves_saved_configuration(self):
        self.store.save(self.first, {'name': '原课程'})
        original = self.path.read_bytes()
        with patch('course_profile.os.replace', side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                self.store.save(self.second, {'name': '新课程'})
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.glob('*.tmp')), [])

    def test_validation_and_bounds(self):
        self.assertEqual(normalize_course_profile(None), {})
        self.assertEqual(normalize_course_profile({'name': 42, 'topic': '  优化  '}), {'topic': '优化'})
        self.assertEqual(len(normalize_course_profile({'background': 'x' * 3000})['background']), 2000)
        with self.assertRaises(ValueError):
            self.store.save('../../other', {'name': '课程'})
        with self.assertRaises(ValueError):
            self.store.save(self.first, {'name': '  '})
        with self.assertRaises(KeyError):
            self.store.select(self.first)

    def test_api_saves_and_loads_project_file(self):
        from fastapi.testclient import TestClient
        import server
        with patch.object(server, 'course_profiles', self.store):
            with TestClient(server.app) as client:
                self.assertEqual(client.get('/api/courses').json()['profiles'], [])
                saved = client.put('/api/courses/' + self.first,
                    json={'name': '统计学', 'background': '本科', 'topic': '假设检验'})
                self.assertEqual(saved.status_code, 200)
                self.assertEqual(saved.json()['activeId'], self.first)
                self.assertEqual(CourseProfileStore(self.path).read()['profiles'][0]['topic'], '假设检验')
                self.assertEqual(client.post('/api/courses/select', json={'activeId': ''}).status_code, 200)
                self.assertEqual(client.get('/api/courses').json()['activeId'], '')
                self.assertEqual(client.put('/api/courses/' + self.second, json={'name': ''}).status_code, 422)
                self.assertEqual(client.post('/api/courses/select', json={'activeId': self.second}).status_code, 404)
                self.assertEqual(client.post('/api/courses/select', json={'activeId': []}).status_code, 422)
                self.assertEqual(client.delete('/api/courses/' + self.first).json()['profiles'], [])
                created = client.post('/api/courses', json={'name': '机器学习'})
                self.assertEqual(created.status_code, 200)
                self.assertEqual(created.json()['profiles'][0]['name'], '机器学习')
                self.assertEqual(str(uuid.UUID(created.json()['activeId'])), created.json()['activeId'])


if __name__ == '__main__':
    unittest.main()
