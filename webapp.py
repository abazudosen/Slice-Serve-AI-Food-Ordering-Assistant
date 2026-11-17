from __future__ import annotations

import hashlib
import hmac
import logging
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from threading import Event, Lock
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from agents import BagelAgent, PizzaAgent
from app_models import ChatRequest, ChatResponse, Session
from session_store import SessionRepository


logger = logging.getLogger("pizza_order_agent")
app = FastAPI(title="Slice Serve AI Food Ordering Assistant)

BASE_DIR = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

SESSION_REPOSITORY = SessionRepository()
AGENT = PizzaAgent()
BAGEL_AGENT = BagelAgent()
ACTIVE_TURNS: dict[tuple[str, str], Event] = {}
TURN_LOCKS: dict[tuple[str, str], Lock] = {}
TURN_REGISTRY_LOCK = Lock()


@app.middleware("http")
async def observe_request(request: Request, call_next):
    request_id = str(uuid.uuid4())
    started_at = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        route = request.scope.get("route")
        route_path = getattr(route, "path", "unknown")
        logger.info(
            "request_complete request_id=%s route=%s method=%s status=%s duration_ms=%.2f",
            request_id,
            route_path,
            request.method,
            status_code,
            (time.perf_counter() - started_at) * 1000,
        )


def _owner_cookie(owner_id: str, secret: bytes) -> str:
    signature = hmac.new(secret, owner_id.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{owner_id}.{signature}"


def demo_owner(request: Request, response: Response) -> str:
    cookie = request.cookies.get("demo_owner", "")
    owner_id, separator, signature = cookie.partition(".")
    secret = SESSION_REPOSITORY.cookie_secret
    valid = (
        bool(separator)
        and len(owner_id) <= 64
        and hmac.compare_digest(signature, hmac.new(secret, owner_id.encode("utf-8"), hashlib.sha256).hexdigest())
    )
    if not valid:
        owner_id = str(uuid.uuid4())

    response.set_cookie(
        "demo_owner",
        _owner_cookie(owner_id, secret),
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        max_age=60 * 60 * 24 * 30,
        path="/",
    )
    return owner_id


OwnerId = Annotated[str, Depends(demo_owner)]


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(BASE_DIR / "static" / "index.html"))


@app.post("/api/chat", response_model=ChatResponse)
def chat(request: ChatRequest, owner_id: OwnerId) -> ChatResponse:
    flow = request.flow.strip().lower()
    if flow not in {"pizza", "bagel"}:
        raise HTTPException(status_code=400, detail="Flow must be 'pizza' or 'bagel'")

    session_id = request.session_id or str(uuid.uuid4())
    key = (owner_id, session_id)
    with TURN_REGISTRY_LOCK:
        previous_turn = ACTIVE_TURNS.get(key)
        if previous_turn is not None:
            previous_turn.set()
        cancellation_event = Event()
        ACTIVE_TURNS[key] = cancellation_event
        turn_lock = TURN_LOCKS.setdefault(key, Lock())

    try:
        with turn_lock:
            if cancellation_event.is_set():
                session = SESSION_REPOSITORY.get(session_id, owner_id) or Session(flow=flow)
                reply = "That turn was canceled because a newer request replaced it."
            else:
                session = SESSION_REPOSITORY.get(session_id, owner_id)
                if session is None or session.flow != flow:
                    session = Session(flow=flow)

                agent = BAGEL_AGENT if flow == "bagel" else AGENT
                reply = agent.run_turn(session, request.message, cancellation_event)

                with TURN_REGISTRY_LOCK:
                    may_commit = (
                        ACTIVE_TURNS.get(key) is cancellation_event
                        and not cancellation_event.is_set()
                    )
                    if may_commit:
                        del ACTIVE_TURNS[key]

                if not may_commit:
                    session = SESSION_REPOSITORY.get(session_id, owner_id) or Session(flow=flow)
                    reply = "That turn was canceled because a newer request replaced it."
                elif not SESSION_REPOSITORY.save(session_id, session, owner_id):
                    raise HTTPException(status_code=409, detail="This session could not be saved.")

        return ChatResponse(
            session_id=session_id,
            flow=flow,
            reply=reply,
            state=asdict(session.state),
            trace=session.trace[-5:],
        )
    finally:
        with TURN_REGISTRY_LOCK:
            if ACTIVE_TURNS.get(key) is cancellation_event:
                del ACTIVE_TURNS[key]


@app.get("/api/sessions")
def list_sessions(
    owner_id: OwnerId,
    flow: str | None = Query(default=None),
) -> list[dict[str, str]]:
    if flow is not None and flow not in {"pizza", "bagel"}:
        raise HTTPException(status_code=400, detail="Flow must be 'pizza' or 'bagel'")
    return SESSION_REPOSITORY.list_sessions(owner_id, flow=flow)


@app.get("/api/session/{session_id}", response_model=ChatResponse)
def get_session(session_id: str, owner_id: OwnerId) -> ChatResponse:
    session = SESSION_REPOSITORY.get(session_id, owner_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return ChatResponse(
        session_id=session_id,
        flow=session.flow,
        reply="Session loaded.",
        state=asdict(session.state),
        trace=session.trace[-5:],
    )


@app.delete("/api/session/{session_id}", status_code=204)
def delete_session(session_id: str, owner_id: OwnerId) -> Response:
    key = (owner_id, session_id)
    with TURN_REGISTRY_LOCK:
        active_turn = ACTIVE_TURNS.get(key)
        if active_turn is not None:
            active_turn.set()
        turn_lock = TURN_LOCKS.get(key)

    if turn_lock is None:
        deleted = SESSION_REPOSITORY.delete(session_id, owner_id)
    else:
        with turn_lock:
            deleted = SESSION_REPOSITORY.delete(session_id, owner_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    return Response(status_code=204)


@app.post("/api/session/{session_id}/cancel", status_code=202)
def cancel_turn(session_id: str, owner_id: OwnerId) -> dict[str, bool]:
    with TURN_REGISTRY_LOCK:
        cancellation_event = ACTIVE_TURNS.get((owner_id, session_id))
        if cancellation_event is not None:
            cancellation_event.set()
    return {"cancelled": cancellation_event is not None}
