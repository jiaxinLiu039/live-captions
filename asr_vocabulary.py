"""Extract English course terms and reuse model/account-specific cloud vocabularies."""

import asyncio
import hashlib
import json
import os
import re
import tempfile
import threading
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import dashscope
from dashscope.audio.asr import VocabularyService


def english_course_terms(glossary: str) -> list[str]:
    terms = []
    seen = set()
    for line in (glossary or "").splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        line = re.sub(r'^(?:[-*•]\s+|\d+[.)]\s+)', '', line)
        term = re.split(r'\s*(?:->|=|＝|→|:|：)\s*', line, maxsplit=1)[0].strip()
        term = term.replace('’', "'").replace('–', '-').replace('—', '-')
        # Keep Latin-language terms, without accidentally stripping Chinese text.
        if any(char.isalpha() and 'LATIN' not in unicodedata.name(char, '') for char in term):
            continue
        term = unicodedata.normalize('NFKD', term).encode('ascii', 'ignore').decode()
        term = ' '.join(term.split())
        if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .,'()_+\-/]*", term)
                or not re.search(r'[A-Za-z]', term) or len(term.split()) > 7):
            continue
        key = term.casefold()
        if key not in seen:
            seen.add(key)
            terms.append(term)
            if len(terms) == 500:
                break
    return terms


@dataclass
class VocabularyResult:
    vocabulary_id: str | None = None
    count: int = 0
    warning: str = ''


class ASRVocabularyCache:
    def __init__(self, path: Path, service_factory=None):
        self.path = path
        self.service_factory = service_factory
        self._lock = threading.Lock()
        self._entries = None

    def _load(self):
        if self._entries is None:
            entries = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}
            if not isinstance(entries, dict):
                raise ValueError('Invalid vocabulary cache')
            self._entries = entries
        return self._entries

    def _write(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent,
                                             suffix='.tmp', delete=False) as handle:
                temp_path = Path(handle.name)
                json.dump(self._entries, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()

    def _prepare(self, course_id, terms, model, api_key, weight):
        # Include account and endpoint identity, without persisting the API key.
        identity = [model, course_id, api_key, dashscope.base_http_api_url]
        key = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
        vocabulary = [{'text': text, 'weight': weight, 'lang': 'en'} for text in terms]
        signature = hashlib.sha256(json.dumps(vocabulary, sort_keys=True).encode()).hexdigest()
        with self._lock:
            entries = self._load()
            service = (self.service_factory or VocabularyService)(api_key=api_key, timeout=3)
            entry = entries.get(key)
            vocabulary_id = entry.get('vocabulary_id') if isinstance(entry, dict) else None
            if vocabulary_id:
                try:
                    service.query_vocabulary(vocabulary_id)
                except Exception as exc:
                    # A deleted resource can be recreated; auth/network errors must not
                    # result in duplicate resources on every recording attempt.
                    status_code = getattr(exc, 'status_code', getattr(exc, '_status_code', None))
                    if status_code != 404:
                        raise
                    vocabulary_id = None
            if vocabulary_id and entry.get('signature') != signature:
                service.update_vocabulary(vocabulary_id, vocabulary)
            elif not vocabulary_id:
                vocabulary_id = service.create_vocabulary(
                    target_model=model, prefix='caption', vocabulary=vocabulary)
            if not isinstance(vocabulary_id, str) or not vocabulary_id:
                raise ValueError('No vocabulary ID returned')
            # Save the ID even while compilation is pending, so a later request can
            # reuse it instead of consuming another vocabulary slot.
            entries[key] = {'vocabulary_id': vocabulary_id, 'signature': signature}
            warning = ''
            try:
                self._write()
            except OSError:
                warning = '课程热词已加载，但缓存文件保存失败；请检查项目目录写入权限。'
            deadline = time.monotonic() + 5
            while True:
                result = service.query_vocabulary(vocabulary_id)
                if result.get('status') == 'OK':
                    return VocabularyResult(vocabulary_id, len(terms), warning)
                if result.get('status') in {'FAILED', 'ERROR'} or time.monotonic() >= deadline:
                    raise RuntimeError('Vocabulary compilation failed or timed out')
                time.sleep(0.25)

    async def prepare(self, course_id, profile, model, api_key, weight=4, timeout=15):
        terms = english_course_terms(profile.get('glossary', ''))
        if not terms:
            return VocabularyResult()
        if not api_key:
            return VocabularyResult(warning='课程热词未加载：请配置阿里云 API Key。')
        try:
            return await asyncio.wait_for(asyncio.to_thread(
                self._prepare, course_id, terms, model, api_key, weight), timeout=timeout)
        except Exception:
            return VocabularyResult(warning='课程热词加载失败，本次使用英语通用识别；请检查阿里云热词权限、网络或词表额度后重新开始。')
