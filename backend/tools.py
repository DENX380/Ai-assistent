"""
Инструменты ("руки") для модели: поиск в интернете, переход по ссылкам,
работа с файлами, выполнение команд и управление виджетами интерфейса.
"""

import os
import subprocess

import requests
from bs4 import BeautifulSoup

# Пакет duckduckgo_search был переименован в ddgs — пробуем новое имя,
# и откатываемся на старое для совместимости, если у пользователя ещё
# не обновлён пакет.
try:
    from ddgs import DDGS
except ImportError:
    from duckduckgo_search import DDGS

from . import board_context
from . import widgets_store as ws

MAX_TEXT_LEN = 4000

# --- Поиск и браузинг ---------------------------------------------------


def web_search(query: str, max_results: int = 5) -> str:
    """Ищет в интернете через DuckDuckGo, без API-ключа."""
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
    except Exception as e:
        return f"Ошибка поиска: {e}"

    if not results:
        return "Ничего не найдено."

    lines = []
    for i, r in enumerate(results, 1):
        lines.append(
            f"{i}. {r.get('title')}\n   URL: {r.get('href')}\n   {r.get('body')}"
        )
    return "\n".join(lines)


_CAPTCHA_MARKERS = (
    "captcha", "recaptcha", "hcaptcha", "verify you are human",
    "подтвердите, что вы не робот", "я не робот", "cloudflare",
    "checking your browser",
)


def open_url(url: str) -> str:
    """Загружает страницу по ссылке и возвращает извлечённый читаемый текст."""
    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
    except Exception as e:
        return f"Ошибка загрузки страницы: {e}"

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    text = " ".join(soup.get_text(separator=" ").split())
    lowered = text.lower()
    if any(marker in lowered for marker in _CAPTCHA_MARKERS) and len(text) < 2000:
        return (
            "Эта страница показывает проверку на робота (капчу) и не отдаёт "
            "содержимое автоматическим запросам. Автоматический обход капч не "
            "поддерживается — откройте ссылку сами в браузере: " + url
        )
    return text[:MAX_TEXT_LEN]


# --- Файловая система -----------------------------------------------------


def list_dir(path: str = ".") -> str:
    try:
        entries = os.listdir(path)
    except Exception as e:
        return f"Ошибка: {e}"
    return "\n".join(entries) if entries else "(пусто)"


def read_file(path: str, max_chars: int = 4000) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read(max_chars)
    except Exception as e:
        return f"Ошибка чтения файла: {e}"


def write_file(path: str, content: str) -> str:
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Файл {path} записан ({len(content)} символов)."
    except Exception as e:
        return f"Ошибка записи файла: {e}"


MAX_FIND_RESULTS = 30
MAX_FIND_DEPTH = 6
SKIP_DIR_NAMES = {".git", "node_modules", "__pycache__", ".venv", "venv", ".idea"}


def find_files(query: str, path: str = ".", search_content: bool = False) -> str:
    """
    Ищет файлы по подстроке в имени начиная от path (рекурсивно, с разумным
    ограничением глубины и числа результатов). Если search_content=True —
    дополнительно ищет подстроку query и внутри текстовых файлов (медленнее).
    """
    query_lower = (query or "").lower()
    if not query_lower:
        return "Ошибка: не указана строка для поиска."

    root = os.path.abspath(path)
    if not os.path.isdir(root):
        return f"Ошибка: папка не найдена: {path}"

    matches = []
    root_depth = root.rstrip(os.sep).count(os.sep)

    for current_dir, dirnames, filenames in os.walk(root):
        depth = current_dir.rstrip(os.sep).count(os.sep) - root_depth
        if depth >= MAX_FIND_DEPTH:
            dirnames[:] = []  # не углубляемся дальше
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES and not d.startswith(".")]

        for name in filenames:
            full = os.path.join(current_dir, name)
            hit_reason = None
            if query_lower in name.lower():
                hit_reason = "имя"
            elif search_content:
                try:
                    with open(full, "r", encoding="utf-8", errors="ignore") as f:
                        if query_lower in f.read(200_000).lower():
                            hit_reason = "содержимое"
                except Exception:
                    pass  # бинарные/недоступные файлы просто пропускаем

            if hit_reason:
                matches.append(f"{full}  (совпадение: {hit_reason})")
                if len(matches) >= MAX_FIND_RESULTS:
                    break
        if len(matches) >= MAX_FIND_RESULTS:
            break

    if not matches:
        return f"Ничего не найдено по запросу «{query}» в {root}."
    suffix = f"\n(показаны первые {MAX_FIND_RESULTS} результатов)" if len(matches) >= MAX_FIND_RESULTS else ""
    return "\n".join(matches) + suffix


# --- Команды в терминале ---------------------------------------------------
# run_command обрабатывается ОСОБО в agent.py: если команда не входит в
# "безопасный" список ниже, агент приостанавливается и ждёт подтверждения
# через интерфейс, прежде чем вызвать execute_shell().

SAFE_PREFIXES = (
    "pwd", "whoami", "date", "hostname", "uname", "echo",
    "python --version", "python3 --version",
    "pip list", "pip show", "pip --version",
    "git status", "git log", "git branch", "git diff",
)
UNSAFE_CHARS = set(";&|`$><")


def is_safe_command(command: str) -> bool:
    """
    Эвристический белый список: только команды без опасных метасимволов
    оболочки и начинающиеся с заведомо безопасного, только читающего
    префикса, выполняются без подтверждения. Всё остальное — с подтверждением.
    Это удобство, а не гарантия безопасности: всегда читайте, что показывает
    интерфейс перед подтверждением.
    """
    command = command.strip()
    if not command or any(ch in command for ch in UNSAFE_CHARS):
        return False
    return any(command == p or command.startswith(p + " ") for p in SAFE_PREFIXES)


def execute_shell(command: str) -> str:
    """Реально выполняет команду. Вызывается agent.py только после (авто- или
    ручного) подтверждения — не регистрируйте это напрямую как публичный
    инструмент без гейта подтверждения."""
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=60
        )
        output = (result.stdout or "") + (result.stderr or "")
        return output[:4000] if output.strip() else "(команда выполнена, вывода нет)"
    except Exception as e:
        return f"Ошибка выполнения: {e}"


# --- Виджеты интерфейса -----------------------------------------------------
# Модель ничего не знает про "доски" — она всегда работает с той, что сейчас
# открыта у пользователя (board_context.get_current_board()), сервер выставляет
# это перед обработкой каждого запроса.


def _coerce_int(value, default: int) -> int:
    """Модели иногда присылают числа как строки ('320' вместо 320) или
    вовсе забывают параметр — не роняем вызов инструмента из-за этого."""
    if value is None:
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def create_widget(title: str = "Без названия", type: str = "text", content: str = "",
                   x=40, y=40, w=320, h=220) -> str:
    """Создаёт виджет на холсте интерфейса. type: 'text', 'html' или 'python'."""
    normalized_type = (type or "text").strip().lower()
    if normalized_type not in ("text", "html", "python"):
        return "Ошибка: параметр type должен быть 'text', 'html' или 'python'."
    widget = ws.create_widget(
        board_context.get_current_board(),
        title or "Без названия",
        normalized_type,
        content or "",
        _coerce_int(x, 40), _coerce_int(y, 40),
        _coerce_int(w, 320), _coerce_int(h, 220),
    )
    return f"Виджет создан, id={widget['id']}"


def update_widget(id: str, content: str = None, title: str = None) -> str:
    """Обновляет содержимое и/или заголовок существующего виджета по id."""
    widget = ws.update_widget(board_context.get_current_board(), id, content=content, title=title)
    if widget is None:
        return f"Ошибка: виджет с id={id} не найден."
    return f"Виджет {id} обновлён."


def delete_widget(id: str) -> str:
    """Удаляет виджет по id."""
    ok = ws.delete_widget(board_context.get_current_board(), id)
    return f"Виджет {id} удалён." if ok else f"Ошибка: виджет с id={id} не найден."


def list_widgets() -> str:
    """Возвращает список текущих виджетов (id, заголовок, тип)."""
    widgets = ws.list_widgets(board_context.get_current_board())
    if not widgets:
        return "Виджетов пока нет."
    return "\n".join(f"{w['id']}: {w['title']} ({w['type']})" for w in widgets)


def get_widget(id: str) -> str:
    """Возвращает ПОЛНОЕ содержимое виджета по id (без обрезки), в отличие
    от краткого превью, которое передаётся в системном промпте."""
    widget = ws.get_widget(board_context.get_current_board(), id)
    if widget is None:
        return f"Ошибка: виджет с id={id} не найден."
    return (
        f"id={widget['id']}\ntitle={widget['title']}\ntype={widget['type']}\n"
        f"--- содержимое ---\n{widget['content']}"
    )


# --- Реестр инструментов, доступных модели через action-протокол -----------

TOOLS = {
    "web_search": web_search,
    "open_url": open_url,
    "list_dir": list_dir,
    "read_file": read_file,
    "write_file": write_file,
    "find_files": find_files,
    "create_widget": create_widget,
    "update_widget": update_widget,
    "delete_widget": delete_widget,
    "list_widgets": list_widgets,
    "get_widget": get_widget,
    # "run_command" намеренно не в этом словаре — см. agent.py
}

TOOLS_DESCRIPTION = """Доступные инструменты:
- web_search(query): поиск в интернете, возвращает список результатов с URL.
  Используй, когда нужна информация из интернета, новости, факты, сравнения.
- open_url(url): переходит по ссылке из web_search и возвращает текст страницы
  целиком — используй, когда краткого описания из web_search недостаточно.
  Если сайт показывает капчу/проверку на робота — вернётся честное сообщение
  об этом; автоматический обход капч не поддерживается, предложи пользователю
  открыть ссылку самому.
- list_dir(path): список файлов и папок по указанному пути
- find_files(query, path, search_content): ищет файлы по подстроке в имени
  (и, если search_content=true, по содержимому) начиная от path рекурсивно.
  Используй это, когда пользователь просит НАЙТИ файл, а не когда знает
  точный путь.
- read_file(path): читает содержимое файла по ТОЧНОМУ пути (если путь
  неизвестен — сначала find_files или list_dir)
- write_file(path, content): создаёт файл или полностью перезаписывает его
- run_command(command): выполняет команду в терминале (может потребоваться подтверждение пользователя)
- create_widget(title, type, content, x, y, w, h): создаёт виджет на экране пользователя.
  type = "text" — обычный текст; type = "html" — произвольный HTML/CSS/JS,
  который будет показан в изолированном iframe (открытие новых вкладок
  разрешено — см. ниже); type = "python" — редактор кода со встроенным
  Python-компилятором (выполняется в браузере пользователя через Pyodide,
  БЕЗ доступа к файлам и системе пользователя) — content в этом случае это
  исходный код на Python, который окажется в редакторе. x,y,w,h —
  координаты и размер в пикселях (необязательны, есть значения по умолчанию,
  можно не указывать вовсе).
  Если в html-виджете нужна кнопка/ссылка на внешний сайт — используй
  <a href="..." target="_blank" rel="noopener">, а не onclick с
  location.href: так сайт откроется в новой вкладке, что работает надёжно
  (многие сайты всё равно запрещают показывать себя прямо внутри iframe).
- update_widget(id, content, title): обновляет существующий виджет по id.
  Используй это, а не create_widget, если пользователь просит изменить/
  дополнить уже имеющийся на экране виджет (например, дописать код в
  существующий python-виджет).
- delete_widget(id): удаляет виджет по id
- list_widgets(): показывает список всех текущих виджетов на экране
- get_widget(id): возвращает ПОЛНОЕ содержимое виджета (в системном промпте
  ты видишь только короткое превью — используй get_widget, если тебе нужно
  увидеть весь код/текст виджета целиком перед тем, как его менять)

Как выбирать инструмент по смыслу запроса пользователя:
- "найди в интернете / что известно про / последние новости о" → web_search
  (и open_url на самый релевантный результат, если нужны подробности)
- "найди файл / где у меня лежит" → find_files
- "прочитай файл X" → read_file (путь должен быть точным; если не уверен — сначала find_files/list_dir)
- "создай/сохрани файл" → write_file
- "сделай виджет/калькулятор/заметку/страницу с..." → create_widget
- "допиши/поправь/обнови то, что уже на экране" → update_widget (НЕ create_widget)
- "убери/удали виджет" → delete_widget
- "что сейчас на экране" → list_widgets (или get_widget для полного содержимого одного виджета)

Примеры правильных ответов (формат ответа объяснён ниже, здесь — только
чтобы показать, как выглядит выбор инструмента и аргументов):
Пользователь: "найди последние новости про SpaceX"
Ответ: {"action": "web_search", "args": {"query": "SpaceX последние новости"}}

Пользователь: "сделай виджет-заметку с планом на день"
Ответ: {"action": "create_widget", "args": {"title": "План на день", "type": "text", "content": "1. ...\\n2. ..."}}

Пользователь: "найди у меня файл report.docx"
Ответ: {"action": "find_files", "args": {"query": "report.docx", "path": "."}}

У пользователя есть графический интерфейс с "холстом", на котором виджеты
отображаются как перетаскиваемые карточки. Актуальный список виджетов и их
id тебе также передаётся отдельно перед каждым запросом — сверяйся с ним,
чтобы не создавать дубликаты и обновлять именно нужный виджет. Если нужно
увидеть содержимое виджета целиком (не только превью) — вызови get_widget."""
