"""
Логирование диалога и действий агента в data/logs/YYYY-MM-DD.jsonl —
по одному файлу в день, каждая строка — отдельное событие в формате JSON.
"""

import datetime
import json
import os

LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "logs")


def log_event(session_id: str, event: dict) -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    path = os.path.join(LOG_DIR, f"{datetime.date.today().isoformat()}.jsonl")
    record = {"ts": datetime.datetime.now().isoformat(), "session": session_id, **event}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
