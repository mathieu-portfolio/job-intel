from __future__ import annotations

from pathlib import Path
from threading import Event
from urllib.parse import parse_qs
from uuid import uuid4

from fastapi import Request

def _positive_int(value: str | None, default: int) -> int:
    try:
        parsed = int(value or "")
    except ValueError:
        return default
    return parsed if parsed > 0 else default

def _optional_positive_int(value: str | None) -> int | None:
    cleaned = (value or "").strip()
    if not cleaned:
        return None
    parsed = _positive_int(cleaned, 0)
    return parsed or None

def _optional_path(value: str | None) -> Path | None:
    cleaned = (value or "").strip()
    return Path(cleaned) if cleaned else None

def _workflow_notice(kind: str, title: str, summary: dict[str, object], messages: list[str] | None = None) -> dict[str, object]:
    return {
        "kind": kind,
        "title": title,
        "summary": summary,
        "messages": messages or [],
    }

async def _form_data(request: Request) -> dict[str, str]:
    body = (await request.body()).decode("utf-8")
    parsed = parse_qs(body, keep_blank_values=True)
    return {key: values[-1] for key, values in parsed.items() if values}

def _consume_workflow_notice(request: Request) -> dict[str, object] | None:
    notice = request.app.state.workflow_notice
    request.app.state.workflow_notice = None
    return notice

def _workflow_token(request: Request) -> str:
    token = request.headers.get("x-workflow-token", "").strip()
    return token or uuid4().hex

def _cancellation_event(request: Request, token: str) -> Event:
    event = Event()
    request.app.state.workflow_cancellations[token] = event
    return event

def _clear_cancellation_event(request: Request, token: str) -> None:
    request.app.state.workflow_cancellations.pop(token, None)

def _record_workflow_progress(request: Request, token: str, message: str) -> None:
    progress = {"message": message}
    with request.app.state.workflow_progress_lock:
        previous = dict(request.app.state.workflow_progress.get(token, {}))
    total = previous.get("total")
    current = previous.get("current")

    if message.startswith("Evaluating "):
        prefix = message.removeprefix("Evaluating ").split(":", 1)[0]
        try:
            current_text, total_text = prefix.split("/", 1)
            current = int(current_text)
            total = int(total_text)
            progress["current"] = current
            progress["total"] = total
            progress["remaining"] = max(total - current + 1, 0)
        except ValueError:
            pass
    elif message.startswith("Model response parsed") and total is not None and current is not None:
        progress["current"] = current
        progress["total"] = total
        progress["remaining"] = max(total - current, 0)
    elif message.startswith("Saved ") and " rankings" in message and total is not None:
        progress["current"] = total
        progress["total"] = total
        progress["remaining"] = 0
    elif "AI-evaluated " in message:
        try:
            total = int(message.split("AI-evaluated ", 1)[1].split(";", 1)[0])
            progress["current"] = 0
            progress["total"] = total
            progress["remaining"] = total
        except ValueError:
            pass
    elif message.startswith("Processed ") and "/" in message and " newly explored offers" in message:
        prefix = message.removeprefix("Processed ").split(" newly explored offers", 1)[0]
        try:
            current_text, total_text = prefix.split("/", 1)
            current = int(current_text)
            total = int(total_text)
            progress["current"] = current
            progress["total"] = total
            progress["remaining"] = max(total - current, 0)
        except ValueError:
            pass

    with request.app.state.workflow_progress_lock:
        merged = dict(previous)
        merged.update(progress)
        request.app.state.workflow_progress[token] = merged

def _clear_workflow_progress(request: Request, token: str) -> None:
    with request.app.state.workflow_progress_lock:
        request.app.state.workflow_progress.pop(token, None)
