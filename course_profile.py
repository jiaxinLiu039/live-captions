"""Course reference data and atomic project-local configuration storage."""

import json
import os
import tempfile
import threading
import uuid
from pathlib import Path

COURSE_LIMITS = {"name": 200, "background": 2000, "topic": 1000, "glossary": 6000}


def normalize_course_profile(value) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    profile = {key: value[key].strip()[:limit]
               for key, limit in COURSE_LIMITS.items() if isinstance(value.get(key), str)}
    return {key: text for key, text in profile.items() if text}


class CourseProfileStore:
    def __init__(self, path: Path, presets_path: Path | None = None):
        self.path = path
        self.presets_path = presets_path
        self._lock = threading.Lock()

    def _read(self):
        source = self.path if self.path.exists() else self.presets_path
        if source is None or not source.exists():
            return {"profiles": [], "activeId": ""}
        data = json.loads(source.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict) or not isinstance(data.get("profiles"), list):
            raise ValueError("课程配置文件格式无效")
        profiles = []
        seen = set()
        for item in data["profiles"]:
            if not isinstance(item, dict):
                raise ValueError("课程配置文件格式无效")
            profile_id = self._validate_id(item.get("id", ""))
            if profile_id in seen:
                raise ValueError("课程配置 ID 重复")
            seen.add(profile_id)
            profile = normalize_course_profile(item)
            if not profile.get("name"):
                raise ValueError("课程配置缺少名称")
            profiles.append({**profile, "id": profile_id})
        active_id = data.get("activeId", "")
        return {"profiles": profiles, "activeId": active_id if isinstance(active_id, str) and active_id in seen else ""}

    @staticmethod
    def _validate_id(value):
        try:
            return str(uuid.UUID(value))
        except (ValueError, TypeError, AttributeError):
            raise ValueError("课程 ID 无效") from None

    def _write(self, state):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             suffix=".tmp", delete=False) as handle:
                temp_path = Path(handle.name)
                json.dump(state, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()

    def read(self):
        with self._lock:
            return self._read()

    def save(self, profile_id, value):
        profile_id = self._validate_id(profile_id)
        profile = normalize_course_profile(value)
        if not profile.get("name"):
            raise ValueError("请填写课程名称")
        with self._lock:
            state = self._read()
            state["profiles"] = [p for p in state["profiles"] if p["id"] != profile_id]
            state["profiles"].append({**profile, "id": profile_id})
            state["activeId"] = profile_id
            self._write(state)
            return state

    def select(self, profile_id):
        with self._lock:
            state = self._read()
            if profile_id and not any(p["id"] == profile_id for p in state["profiles"]):
                raise KeyError("课程不存在，请重新加载")
            state["activeId"] = profile_id
            self._write(state)
            return state

    def delete(self, profile_id):
        profile_id = self._validate_id(profile_id)
        with self._lock:
            state = self._read()
            state["profiles"] = [p for p in state["profiles"] if p["id"] != profile_id]
            if state["activeId"] == profile_id:
                state["activeId"] = ""
            self._write(state)
            return state
