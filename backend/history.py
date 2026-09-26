"""
Сохранение истории диалога в data/conversation.json, чтобы при перезапуске
сервера чат в интерфейсе можно было восстановить.
"""

from __future__ import annotations

import datetime
import json
import os
import threading

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
HISTORY_FILE = os.path.join(DATA_DIR, "conversation.json")
_lock = threading.Lock()
_cache: dict | None = None


def _ensure_file():
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f)


def _load_all() -> dict:
    """Кешируем в памяти — история читается на каждое сообщение чата,
    незачем каждый раз парсить JSON с диска заново в рамках одного процесса."""
    global _cache
    with _lock:
        if _cache is not None:
            return _cache
        _ensure_file()
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            _cache = json.load(f)
        return _cache


def _save_all(data: dict) -> None:
    global _cache
    _ensure_file()
    with _lock:
        _cache = data
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


def load_history(session_id: str) -> list:
    return _load_all().get(session_id, [])


def append_message(session_id: str, role: str, content: str) -> None:
    data = _load_all()
    data.setdefault(session_id, []).append(
        {"role": role, "content": content, "ts": datetime.datetime.now().isoformat()}
    )
    _save_all(data)


def clear_history(session_id: str) -> None:
    data = _load_all()
    data[session_id] = []
    _save_all(data)
