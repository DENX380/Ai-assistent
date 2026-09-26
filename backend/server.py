"""
FastAPI-сервер: отдаёт фронтенд, стримит ответы агента через
Server-Sent Events (SSE), хранит виджеты (по доскам) и историю чата
(тоже по доскам — чтобы не путать контекст между разными полями с виджетами).
"""

import json
from pathlib import Path

from fastapi import Body, FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import agent, confirmations, history, llm_ollama, logger, widgets_store

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"

app = FastAPI(title="Local AI Agent")


class ChatRequest(BaseModel):
    model: str
    message: str
    board_id: str = widgets_store.DEFAULT_BOARD_ID


class ConfirmRequest(BaseModel):
    id: str
    approved: bool


class BoardCreateRequest(BaseModel):
    name: str = "Новая доска"


class BoardRenameRequest(BaseModel):
    name: str


# Типы событий, которые не логируем целиком (слишком часто/объёмно)
_SKIP_LOG_TYPES = {"assistant_delta"}


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


# --- Модели ------------------------------------------------------------


@app.get("/api/models")
def get_models():
    try:
        return {"models": llm_ollama.list_models()}
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": str(e)}, status_code=500)


# --- Доски (несколько независимых полей с виджетами) --------------------


@app.get("/api/boards")
def get_boards():
    return {"boards": widgets_store.list_boards()}


@app.post("/api/boards")
def create_board(req: BoardCreateRequest):
    return widgets_store.create_board(req.name)


@app.patch("/api/boards/{board_id}")
def rename_board(board_id: str, req: BoardRenameRequest):
    ok = widgets_store.rename_board(board_id, req.name)
    if not ok:
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"ok": True}


@app.delete("/api/boards/{board_id}")
def delete_board(board_id: str):
    ok = widgets_store.delete_board(board_id)
    if not ok:
        return JSONResponse({"error": "нельзя удалить последнюю/основную доску"}, status_code=400)
    return {"ok": True}


# --- Виджеты (в рамках конкретной доски, ?board_id=...) ------------------


@app.get("/api/widgets")
def get_widgets(board_id: str = widgets_store.DEFAULT_BOARD_ID):
    return {"widgets": widgets_store.list_widgets(board_id)}


@app.patch("/api/widgets/{wid}")
def patch_widget(wid: str, board_id: str = widgets_store.DEFAULT_BOARD_ID, payload: dict = Body(...)):
    widget = widgets_store.update_widget(board_id, wid, **payload)
    if widget is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return widget


@app.post("/api/widgets/{wid}/beacon")
async def beacon_save_widget(wid: str, request: Request, board_id: str = widgets_store.DEFAULT_BOARD_ID):
    """
    Приём "на прощание": браузер шлёт это через navigator.sendBeacon при
    закрытии/перезагрузке вкладки, чтобы не потерять несохранённые правки
    в редакторе. Обычный fetch на unload не гарантирует доставку — sendBeacon
    для этого и существует.
    """
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        return {"ok": False}
    widget = widgets_store.update_widget(board_id, wid, **payload)
    return {"ok": widget is not None}


@app.delete("/api/widgets/{wid}")
def delete_widget_endpoint(wid: str, board_id: str = widgets_store.DEFAULT_BOARD_ID):
    ok = widgets_store.delete_widget(board_id, wid)
    return {"ok": ok}


# --- Промпт агента (быстрый доступ "что модель видит") -------------------


@app.get("/api/system-prompt")
def get_system_prompt(board_id: str = widgets_store.DEFAULT_BOARD_ID):
    return {"prompt": agent.get_system_prompt_preview(board_id)}


# --- История чата (тоже по доскам) ---------------------------------------


@app.get("/api/history")
def get_history(board_id: str = widgets_store.DEFAULT_BOARD_ID):
    return {"history": history.load_history(board_id)}


@app.post("/api/history/clear")
def clear_history(board_id: str = widgets_store.DEFAULT_BOARD_ID):
    history.clear_history(board_id)
    return {"ok": True}


@app.post("/api/confirm")
def confirm(req: ConfirmRequest):
    ok = confirmations.resolve(req.id, req.approved)
    return {"ok": ok}


@app.post("/api/chat")
async def chat_endpoint(req: ChatRequest):
    session_id = req.board_id  # история чата привязана к доске

    async def event_stream():
        prior_history = history.load_history(session_id)
        history.append_message(session_id, "user", req.message)
        logger.log_event(session_id, {"role": "user", "content": req.message})

        final_text = None
        try:
            async for event in agent.run_agent_stream(
                req.model, req.message, history=prior_history, board_id=req.board_id
            ):
                if event["type"] == "final":
                    final_text = event["text"]
                if event["type"] not in _SKIP_LOG_TYPES:
                    logger.log_event(session_id, event)
                yield _sse(event)
        except Exception as e:  # noqa: BLE001
            yield _sse({"type": "error", "message": str(e)})

        if final_text is not None:
            history.append_message(session_id, "assistant", final_text)

        yield _sse({"type": "done"})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# Отдаём статику фронтенда последним, чтобы не перекрывать /api/* маршруты
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
