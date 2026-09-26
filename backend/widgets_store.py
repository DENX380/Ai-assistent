"""
Хранилище виджетов — сохраняется в data/widgets.json, переживает
перезапуск сервера. Поддерживает несколько "досок" (полей с виджетами):
каждая доска — независимый набор виджетов со своим названием.
"""

from __future__ import annotations

import json
import os
import threading
import uuid

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
WIDGETS_FILE = os.path.join(DATA_DIR, "widgets.json")
DEFAULT_BOARD_ID = "default"
_lock = threading.Lock()
_cache: dict | None = None  # процесс один, файл меняем только мы сами — кешируем в памяти


def _empty_store() -> dict:
    return {"boards": {DEFAULT_BOARD_ID: {"name": "Основная", "widgets": {}}}}


def _migrate_if_old_format(data: dict) -> dict:
    """Старые файлы (до появления досок) — это плоский словарь {id: widget}.
    Заворачиваем их в одну доску по умолчанию, чтобы ничего не потерять."""
    if "boards" in data:
        return data
    if not data:
        return _empty_store()
    return {"boards": {DEFAULT_BOARD_ID: {"name": "Основная", "widgets": data}}}


def _ensure_file():
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(WIDGETS_FILE):
        with open(WIDGETS_FILE, "w", encoding="utf-8") as f:
            json.dump(_empty_store(), f, ensure_ascii=False, indent=2)


def _load() -> dict:
    """
    Возвращает данные из памяти, если уже загружены — избегаем чтения и
    парсинга JSON-файла с диска при каждом обращении (агент запрашивает
    список виджетов на каждом шаге). На диск ходим только при первом
    обращении в этом процессе и сразу после любой записи (_save).
    """
    global _cache
    with _lock:
        if _cache is not None:
            return _cache
        _ensure_file()
        with open(WIDGETS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        data = _migrate_if_old_format(data)
        if DEFAULT_BOARD_ID not in data["boards"]:
            data["boards"][DEFAULT_BOARD_ID] = {"name": "Основная", "widgets": {}}
        _cache = data
        return _cache


def _save(data: dict) -> None:
    global _cache
    _ensure_file()
    with _lock:
        _cache = data
        with open(WIDGETS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


def _board_widgets(data: dict, board_id: str) -> dict:
    board = data["boards"].setdefault(board_id, {"name": "Без названия", "widgets": {}})
    return board["widgets"]


# --- Доски -------------------------------------------------------------


def list_boards() -> list:
    data = _load()
    return [{"id": bid, "name": b["name"]} for bid, b in data["boards"].items()]


def create_board(name: str) -> dict:
    data = _load()
    bid = uuid.uuid4().hex[:8]
    data["boards"][bid] = {"name": name or "Новая доска", "widgets": {}}
    _save(data)
    return {"id": bid, "name": data["boards"][bid]["name"]}


def rename_board(board_id: str, name: str) -> bool:
    data = _load()
    if board_id not in data["boards"]:
        return False
    data["boards"][board_id]["name"] = name
    _save(data)
    return True


def delete_board(board_id: str) -> bool:
    if board_id == DEFAULT_BOARD_ID:
        return False  # хотя бы одна доска должна оставаться всегда
    data = _load()
    if board_id not in data["boards"]:
        return False
    del data["boards"][board_id]
    _save(data)
    return True


# --- Виджеты (в рамках конкретной доски) --------------------------------


def list_widgets(board_id: str = DEFAULT_BOARD_ID) -> list:
    data = _load()
    return list(_board_widgets(data, board_id).values())


def get_widget(board_id: str, wid: str):
    data = _load()
    return _board_widgets(data, board_id).get(wid)


def create_widget(board_id: str, title: str, type_: str, content: str,
                   x: int, y: int, w: int, h: int) -> dict:
    data = _load()
    widgets = _board_widgets(data, board_id)
    wid = uuid.uuid4().hex[:8]
    widget = {
        "id": wid,
        "title": title,
        "type": type_,
        "content": content,
        "x": x,
        "y": y,
        "w": w,
        "h": h,
    }
    widgets[wid] = widget
    _save(data)
    return widget


def update_widget(board_id: str, wid: str, **fields):
    data = _load()
    widgets = _board_widgets(data, board_id)
    if wid not in widgets:
        return None
    for key, value in fields.items():
        if value is not None and key in ("title", "content", "type", "x", "y", "w", "h"):
            widgets[wid][key] = value
    _save(data)
    return widgets[wid]


def delete_widget(board_id: str, wid: str) -> bool:
    data = _load()
    widgets = _board_widgets(data, board_id)
    if wid in widgets:
        del widgets[wid]
        _save(data)
        return True
    return False
