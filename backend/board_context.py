"""
Текущая "доска" (поле с виджетами), в рамках которой работают инструменты
агента. Модель ничего не знает о досках — это чисто интерфейсная сущность:
сервер выставляет её перед обработкой запроса в зависимости от того, какая
доска сейчас открыта у пользователя в браузере.
"""

import contextvars

from . import widgets_store

current_board_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_board_id", default=widgets_store.DEFAULT_BOARD_ID
)


def get_current_board() -> str:
    return current_board_id.get()
