from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.api.admin import router as admin_router
from app.api.s3 import router as s3_router
from app.bootstrap import apply_pending_setup_if_possible
from app.config import settings
from app.database import ObjectDatabaseUnavailableError, init_db


logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


app = FastAPI(title=settings.app_name)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie=settings.session_cookie_name,
    same_site="lax",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup() -> None:
    init_db()
    apply_pending_setup_if_possible()


def _format_validation_messages(errors: list[dict[str, object]]) -> list[str]:
    messages: list[str] = []
    for error in errors:
        raw_loc = error.get("loc", [])
        if isinstance(raw_loc, (list, tuple)):
            loc_parts = [str(part) for part in raw_loc if part not in {"body", "query", "path"}]
        else:
            loc_parts = []

        field_name = " -> ".join(loc_parts)
        message = str(error.get("msg", "Invalid value."))
        if message.startswith("Value error, "):
            message = message.removeprefix("Value error, ")

        messages.append(f"{field_name}: {message}" if field_name else message)
    return messages


@app.exception_handler(RequestValidationError)
async def handle_request_validation_error(_request, exc: RequestValidationError) -> JSONResponse:
    messages = _format_validation_messages(exc.errors())
    return JSONResponse(
        status_code=422,
        content={
            "message": "Please fix the highlighted fields and try again.",
            "detail": messages[0] if len(messages) == 1 else " | ".join(messages),
            "errors": messages,
        },
    )


@app.exception_handler(ObjectDatabaseUnavailableError)
async def handle_object_database_unavailable(_request, exc: ObjectDatabaseUnavailableError) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "message": "Object metadata database is unavailable.",
            "detail": str(exc),
        },
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(admin_router)
app.mount(
    settings.panel_mount_path,
    StaticFiles(directory=str(Path(__file__).resolve().parent / "static"), html=True),
    name="panel",
)
app.include_router(s3_router)
