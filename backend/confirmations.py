"""
Реестр "ожидающих подтверждения" команд. Когда агент хочет выполнить
команду, не входящую в белый список безопасных, сервер приостанавливает
выполнение и ждёт, пока пользователь нажмёт "Разрешить"/"Отклонить" в
интерфейсе — см. POST /api/confirm в server.py.
"""

import asyncio
import uuid

pending: dict[str, "asyncio.Future"] = {}


def create_pending():
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    cid = uuid.uuid4().hex[:8]
    pending[cid] = fut
    return cid, fut


def resolve(cid: str, approved: bool) -> bool:
    fut = pending.pop(cid, None)
    if fut is not None and not fut.done():
        fut.set_result(approved)
        return True
    return False
