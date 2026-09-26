"""
Асинхронный цикл агента (ReAct-стиль). Модель отвечает JSON'ом:
  {"action": "имя_инструмента", "args": {...}}   — вызов инструмента
  {"final_answer": "..."}                         — финальный ответ

Особенности этой версии:
- Запросы к Ollama идут с think=False (см. llm_ollama.py) — отключает
  режим "рассуждения" у гибридных моделей (Qwen3 и подобных), из-за
  которого они иногда не успевают дойти до валидного ответа в рамках
  лимита токенов и выглядят как пустые/сломанные. Специально НЕ используется
  format="json": в сочетании с моделями с мышлением это работает ненадёжно
  (в некоторых версиях Ollama ограничение формата в такой связке не
  применяется вовсе, а в остальных может "запереть" модель на пустом
  ответе) — при тестировании это ухудшало, а не улучшало результат.
- Реальный потоковый вывод: пока модель генерирует токены финального
  ответа, символы транслируются наружу по мере поступления (эффект печати
  в интерфейсе), а не после получения всего ответа целиком.
- run_command приостанавливает выполнение и ждёт подтверждения через
  интерфейс, если команда не входит в белый список безопасных (tools.is_safe_command).
- Помнит предыдущие сообщения диалога (параметр history) и знает текущее
  состояние виджетов на экране пользователя.
- Если модель всё же вернула пустой/некорректный ответ несколько раз
  подряд (MAX_CONSECUTIVE_FAILURES), агент честно объясняет причину вместо
  пустого сообщения — см. _give_up_message.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading

from . import board_context, confirmations, llm_ollama, tools, widgets_store

MAX_STEPS = 10
MAX_HISTORY_MESSAGES = 20  # сколько последних сообщений (не шагов) помнить
MAX_WIDGETS_IN_PROMPT = 6  # чтобы не раздувать промпт, если виджетов много
WIDGET_PREVIEW_LEN = 80
MAX_CONSECUTIVE_FAILURES = 3  # пустые/некорректные ответы подряд, прежде чем сдаться

CONTENT_START = "CONTENT_START"
CONTENT_END = "CONTENT_END"


def _build_system_prompt(board_id: str) -> str:
    """
    Собирает системный промпт заново перед каждым запросом, включая
    актуальный список виджетов ТЕКУЩЕЙ ДОСКИ — чтобы модель знала их id/тип
    и могла редактировать уже существующие через update_widget, а не
    создавать дубликаты. Правила формата ответа идут ПЕРВЫМИ — это заметно
    улучшает соблюдение формата у небольших локальных моделей.
    """
    widgets = widgets_store.list_widgets(board_id)
    if widgets:
        shown = widgets[-MAX_WIDGETS_IN_PROMPT:]
        lines = []
        for w in shown:
            preview = (w.get("content") or "").replace("\n", " ")[:WIDGET_PREVIEW_LEN]
            lines.append(f'- id="{w["id"]}" title="{w["title"]}" type={w["type"]} — {preview or "(пусто)"}')
        extra = len(widgets) - len(shown)
        tail = f"\n(и ещё {extra} виджет(ов), не показаны здесь)" if extra > 0 else ""
        widgets_block = (
            "Текущие виджеты на экране пользователя:\n" + "\n".join(lines) + tail +
            "\n\nЕсли просьба про уже существующий из этого списка виджет — используй "
            "update_widget с ЕГО id, не создавай новый. Но если пользователь просит "
            "создать виджет, похожий на тот, что упоминался раньше в истории диалога, "
            "а его СЕЙЧАС нет в списке выше — значит он был удалён, создай его заново "
            "через create_widget."
        )
    else:
        widgets_block = (
            "Сейчас на экране пользователя нет ни одного виджета. Если из истории "
            "диалога кажется, что какой-то виджет уже был создан раньше, но выше "
            "его нет — значит пользователь его удалил. Если он просит создать такой "
            "же/похожий заново — создай его заново через create_widget, не считай, "
            "что он всё ещё существует."
        )

    return f"""Ты — ассистент с доступом к инструментам на компьютере пользователя.

ФОРМАТ ОТВЕТА — соблюдай строго, без исключений:
1. Чтобы вызвать инструмент, ответь ТОЛЬКО этим JSON и ничем больше:
{{"action": "имя_инструмента", "args": {{"параметр": "значение"}}}}
2. Чтобы дать финальный ответ, ответь ТОЛЬКО этим JSON и ничем больше:
{{"final_answer": "текст ответа"}}
Никакого текста до или после JSON. Ровно один JSON-объект за раз. Не пиши
никаких рассуждений или пояснений вне JSON — сразу отвечай нужным JSON.

ОСОБОЕ ПРАВИЛО ДЛЯ МНОГОСТРОЧНОГО СОДЕРЖИМОГО (параметр content у
create_widget, update_widget, write_file — например, код или HTML):
НЕ пытайся вписывать код или многострочный текст прямо в JSON-строку —
там легко сломать экранирование кавычек и переносов строк. Вместо этого
оставь "content": "" пустым в JSON и добавь содержимое СРАЗУ ПОСЛЕ JSON
в таком виде:

{{"action": "create_widget", "args": {{"title": "Компилятор", "type": "python", "content": ""}}}}
{CONTENT_START}
def factorial(n):
    if n == 0:
        return 1
    return n * factorial(n - 1)

print(factorial(5))
{CONTENT_END}

Если content короткий, без переносов строк и кавычек — можно просто
указать его прямо в JSON, без блока {CONTENT_START}/{CONTENT_END}.

{tools.TOOLS_DESCRIPTION}

{widgets_block}

Напоминание: отвечай СТРОГО одним JSON-объектом (плюс, при необходимости,
блоком {CONTENT_START}/{CONTENT_END} сразу после него), без пояснений вне этого.
"""


def get_system_prompt_preview(board_id: str = widgets_store.DEFAULT_BOARD_ID) -> str:
    """Публичная обёртка над _build_system_prompt — для эндпоинта
    /api/system-prompt, чтобы пользователь мог посмотреть, что именно
    сейчас отправляется модели в качестве системного промпта."""
    return _build_system_prompt(board_id)


FINAL_ANSWER_KEY_RE = re.compile(r'"final_answer"\s*:\s*"')
CONTENT_BLOCK_RE = re.compile(
    re.escape(CONTENT_START) + r"\s*\n(.*?)\n?" + re.escape(CONTENT_END), re.DOTALL
)

_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "/": "/"}


def _repair_json_text(text: str) -> str:
    """
    Некоторые локальные модели вставляют внутрь JSON-строк настоящие
    переносы строк/табуляции вместо \\n/\\t. json.loads на этом падает.
    Проходим по тексту и экранируем такие "сырые" управляющие символы,
    если они оказались внутри строкового литерала.
    """
    out = []
    in_string = False
    escape = False
    for ch in text:
        if in_string:
            if escape:
                out.append(ch)
                escape = False
                continue
            if ch == "\\":
                out.append(ch)
                escape = True
                continue
            if ch == '"':
                in_string = False
                out.append(ch)
                continue
            if ch == "\n":
                out.append("\\n")
                continue
            if ch == "\r":
                out.append("\\r")
                continue
            if ch == "\t":
                out.append("\\t")
                continue
            out.append(ch)
        else:
            if ch == '"':
                in_string = True
            out.append(ch)
    return "".join(out)


def extract_json(text: str):
    """Пытается найти и распарсить JSON-объект в ответе модели, с одной
    попыткой "починки" при частой ошибке (сырые переносы строк в строке)."""
    text = text.strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    candidate = match.group(0)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    try:
        return json.loads(_repair_json_text(candidate))
    except json.JSONDecodeError:
        return None


def _split_content_block(raw: str):
    """
    Если модель использовала блок CONTENT_START/CONTENT_END, возвращает
    (часть_до_блока_для_JSON, содержимое_блока). Иначе (raw, None).
    Это отдельный от JSON канал — не подвержен проблемам экранирования.
    """
    if CONTENT_START not in raw:
        return raw, None
    json_part, _, _ = raw.partition(CONTENT_START)
    m = CONTENT_BLOCK_RE.search(raw)
    content = m.group(1) if m else None
    return json_part, content


async def _iter_blocking_gen(gen_func, *args, **kwargs):
    """
    Превращает блокирующий (синхронный) генератор в асинхронный, выполняя
    его в отдельном потоке и передавая элементы через asyncio.Queue.
    Нужно, потому что requests-стрим сам по себе синхронный.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    SENTINEL = object()

    def worker():
        try:
            for item in gen_func(*args, **kwargs):
                loop.call_soon_threadsafe(queue.put_nowait, item)
        except Exception as e:  # noqa: BLE001
            loop.call_soon_threadsafe(queue.put_nowait, ("__error__", e))
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, SENTINEL)

    threading.Thread(target=worker, daemon=True).start()

    while True:
        item = await queue.get()
        if item is SENTINEL:
            break
        if isinstance(item, tuple) and item and item[0] == "__error__":
            raise item[1]
        yield item


class _JsonStringDecoder:
    """Декодирует символы внутри JSON-строки по мере их поступления кусками,
    корректно обрабатывая экранирование (\\n, \\", ...), даже если escape-
    последовательность окажется разбита между двумя кусками текста."""

    def __init__(self):
        self.pending_escape = False
        self.done = False

    def feed(self, text: str):
        out = []
        for ch in text:
            if self.done:
                break
            if self.pending_escape:
                out.append(_ESCAPES.get(ch, ch))
                self.pending_escape = False
                continue
            if ch == "\\":
                self.pending_escape = True
                continue
            if ch == '"':
                self.done = True
                break
            out.append(ch)
        return out


async def _run_step(model: str, messages: list):
    """
    Выполняет один запрос к модели в потоковом режиме.
    Отдаёт события {"type": "assistant_delta", "text": ch} по мере того,
    как становится понятно, что модель пишет финальный ответ, и в конце —
    ровно одно событие {"type": "__raw_complete__", "raw": <весь ответ>}.
    """
    buffer = ""
    decoder = None  # появится, как только найдём начало строки final_answer

    async for chunk in _iter_blocking_gen(llm_ollama.chat_stream, model, messages):
        buffer += chunk

        if decoder is None:
            m = FINAL_ANSWER_KEY_RE.search(buffer)
            if m:
                # Ключ "final_answer": " найден впервые — начинаем декодировать
                # всё, что идёт после него (в т.ч. то, что уже накопилось).
                decoder = _JsonStringDecoder()
                tail = buffer[m.end():]
                for ch in decoder.feed(tail):
                    yield {"type": "assistant_delta", "text": ch}
        elif not decoder.done:
            for ch in decoder.feed(chunk):
                yield {"type": "assistant_delta", "text": ch}

    yield {"type": "__raw_complete__", "raw": buffer}


async def run_agent_stream(model: str, user_query: str, history: list | None = None,
                            board_id: str = widgets_store.DEFAULT_BOARD_ID):
    """
    Основной цикл агента. Асинхронный генератор событий для SSE:
    tool_call, tool_result, confirm_required, widgets_update,
    assistant_delta, final, error.

    history — предыдущие сообщения диалога (список {"role", "content"}),
    без этого модель не помнит, что было сказано раньше.
    board_id — какая доска (поле с виджетами) сейчас открыта у пользователя;
    инструменты create_widget/update_widget/... работают именно с ней.
    """
    token = board_context.current_board_id.set(board_id)
    try:
        messages = [{"role": "system", "content": _build_system_prompt(board_id)}]

        if history:
            for item in history[-MAX_HISTORY_MESSAGES:]:
                role = item.get("role")
                content = item.get("content", "")
                if role in ("user", "assistant") and content:
                    messages.append({"role": role, "content": content})

        messages.append({"role": "user", "content": user_query})

        consecutive_failures = 0
        last_tool_error = None

        for _ in range(MAX_STEPS):
            raw = None
            try:
                async for ev in _run_step(model, messages):
                    if ev["type"] == "__raw_complete__":
                        raw = ev["raw"]
                    else:
                        yield ev
            except Exception as e:  # noqa: BLE001
                yield {"type": "error", "message": f"Ошибка обращения к модели: {e}"}
                return

            json_part, content_override = _split_content_block(raw)
            parsed = extract_json(json_part)
            has_usable_answer = parsed is not None and ("final_answer" in parsed or "action" in parsed)

            if not has_usable_answer:
                # Модель вернула пустой ответ, невалидный JSON или JSON без
                # нужных полей. Раньше это молча показывалось пользователю
                # как есть (то есть пустым сообщением, если raw был пуст) —
                # теперь пробуем достучаться до модели ещё раз, а если не
                # получается — честно объясняем причину.
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    yield {"type": "final", "text": _give_up_message(raw, last_tool_error)}
                    return
                messages.append({"role": "assistant", "content": raw})
                messages.append({
                    "role": "user",
                    "content": (
                        "Твой предыдущий ответ был пустым или не в нужном формате и "
                        "проигнорирован. Ответь СТРОГО одним JSON-объектом: либо "
                        '{"action": "имя_инструмента", "args": {...}}, либо '
                        '{"final_answer": "текст"}.'
                    ),
                })
                continue

            if "final_answer" in parsed:
                text = parsed["final_answer"]
                if isinstance(text, str) and text.strip():
                    yield {"type": "final", "text": text}
                    return
                # Пустой final_answer — модель (ошибочно) решила, что действие
                # уже не нужно, хотя пользователь ждёт результата.
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    yield {"type": "final", "text": _give_up_message(raw, last_tool_error)}
                    return
                messages.append({"role": "assistant", "content": raw})
                messages.append({
                    "role": "user",
                    "content": (
                        "Ты вернул пустой final_answer. Если для запроса пользователя нужно "
                        "выполнить действие (например, создать виджет) — ответь JSON с полем "
                        "\"action\". Если нужен текстовый ответ — ответь непустым \"final_answer\"."
                    ),
                })
                continue

            consecutive_failures = 0  # получили пригодный к использованию ответ

            tool_name = parsed.get("action")
            args = dict(parsed.get("args", {}) or {})
            if content_override is not None:
                args["content"] = content_override
            yield {"type": "tool_call", "tool": tool_name, "args": args}

            if tool_name == "run_command":
                command = args.get("command", "")
                if not command:
                    observation = "Ошибка: не указана команда."
                elif tools.is_safe_command(command):
                    observation = await asyncio.to_thread(tools.execute_shell, command)
                else:
                    cid, fut = confirmations.create_pending()
                    yield {"type": "confirm_required", "id": cid, "command": command}
                    approved = await _wait_for_confirmation(cid, fut)
                    if approved:
                        observation = await asyncio.to_thread(tools.execute_shell, command)
                    else:
                        observation = "Пользователь отклонил (или не подтвердил вовремя) выполнение команды."
            else:
                observation = await _dispatch_tool(tool_name, args)

            if isinstance(observation, str) and observation.startswith("Ошибка"):
                last_tool_error = observation

            yield {"type": "tool_result", "tool": tool_name, "result": observation}

            if tool_name in ("create_widget", "update_widget", "delete_widget"):
                yield {"type": "widgets_update", "widgets": widgets_store.list_widgets(board_id)}

            messages.append({"role": "assistant", "content": raw})
            messages.append(
                {"role": "user", "content": f"Результат выполнения инструмента:\n{observation}"}
            )

        yield {"type": "final", "text": _give_up_message(None, last_tool_error, exhausted=True)}
    finally:
        board_context.current_board_id.reset(token)


def _give_up_message(raw: str | None, last_tool_error: str | None, exhausted: bool = False) -> str:
    """
    Формирует понятное объяснение вместо пустого сообщения — на случай,
    когда модель так и не смогла дать пригодный ответ (пустой/сломанный
    JSON несколько раз подряд, или закончились шаги агента).
    """
    if last_tool_error:
        return (
            f"Не получилось выполнить действие: {last_tool_error}\n\n"
            "Модель попробовала несколько раз, но не смогла корректно ответить "
            "после этой ошибки. Попробуйте переформулировать запрос."
        )
    if exhausted:
        return (
            "Агент исчерпал лимит шагов, так и не дав финальный ответ. Возможно, "
            "задача слишком сложная для этой модели за один запрос — попробуйте "
            "разбить её на более простые шаги."
        )
    return (
        "Модель несколько раз подряд вернула пустой или некорректно "
        "оформленный ответ вместо ожидаемого JSON. Обычно это значит, что "
        "промпт слишком длинный/сложный для этой модели или сама модель "
        "сейчас нестабильна. Попробуйте сформулировать запрос короче, "
        "повторить его ещё раз или выбрать другую модель."
    )


async def _dispatch_tool(tool_name: str, args: dict) -> str:
    """Диспетчер для всех инструментов, КРОМЕ run_command (у него особая
    обработка прямо в run_agent_stream, т.к. ему нужно уметь приостанавливать
    цикл и слать событие confirm_required)."""
    fn = tools.TOOLS.get(tool_name)
    if fn is None:
        return f"Ошибка: инструмент '{tool_name}' не найден."
    try:
        return await asyncio.to_thread(lambda: fn(**args))
    except Exception as e:  # noqa: BLE001
        return f"Ошибка при выполнении инструмента: {e}"


async def _wait_for_confirmation(cid: str, fut, timeout: float = 120.0) -> bool:
    try:
        return await asyncio.wait_for(fut, timeout=timeout)
    except asyncio.TimeoutError:
        confirmations.pending.pop(cid, None)
        return False
