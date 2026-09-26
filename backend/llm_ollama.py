"""
Обёртка над локальным API Ollama (http://localhost:11434).
Ollama должна быть запущена на вашем ПК: https://ollama.com
"""

import json

import requests

OLLAMA_HOST = "http://localhost:11434"


def list_models():
    """Возвращает список названий моделей, установленных локально в Ollama."""
    resp = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=10)
    resp.raise_for_status()
    data = resp.json()
    return [m["name"] for m in data.get("models", [])]


def chat(model: str, messages: list, temperature: float = 0.2) -> str:
    """Нестримовый запрос — вернёт весь ответ целиком. Полезно для отладки/скриптов."""
    resp = requests.post(
        f"{OLLAMA_HOST}/api/chat",
        json={
            "model": model,
            "messages": messages,
            "stream": False,
            "think": False,
            "options": {"temperature": temperature},
        },
        timeout=300,
    )
    resp.raise_for_status()
    return resp.json()["message"]["content"]


def chat_stream(model: str, messages: list, temperature: float = 0.2):
    """
    Стримовый запрос к Ollama. Генератор отдаёт кусочки текста (токены/группы
    токенов) по мере их генерации моделью — это блокирующий генератор,
    в асинхронном коде его нужно запускать в отдельном потоке.

    think=False отключает режим "рассуждения" у гибридных моделей (Qwen3
    и подобных) — без этого такие модели сначала генерируют длинный
    внутренний ход мыслей и могут вообще не дойти до валидного JSON-ответа
    в рамках лимита токенов, что и выглядит как пустой/сломанный ответ.
    Параметр format="json" сознательно НЕ используется: у Ollama он
    применяет JSON-грамматику только ПОСЛЕ токена конца размышлений, и это
    сочетание не всегда работает надёжно (в некоторых версиях Ollama
    ограничение формата в такой комбинации вовсе не применяется) — так что
    для моделей с мышлением он может сделать только хуже.
    """
    with requests.post(
        f"{OLLAMA_HOST}/api/chat",
        json={
            "model": model,
            "messages": messages,
            "stream": True,
            "think": False,
            "options": {"temperature": temperature},
        },
        stream=True,
        timeout=300,
    ) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line:
                continue
            data = json.loads(line.decode("utf-8"))
            content = data.get("message", {}).get("content", "")
            if content:
                yield content
            if data.get("done"):
                break
