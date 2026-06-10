from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.core.bootstrap import build_container


def _add_localhost_origin(origins: set[str], port: str | int | None) -> None:
    if port is None:
        return
    normalized = str(port).strip()
    if not normalized:
        return
    origins.add(f"http://localhost:{normalized}")
    origins.add(f"http://127.0.0.1:{normalized}")


def allowed_cors_origins() -> list[str]:
    origins: set[str] = set()
    for default_port in ("3000", "3001"):
        _add_localhost_origin(origins, default_port)

    _add_localhost_origin(origins, os.getenv("FRONTEND_PORT"))

    for raw_origin in (os.getenv("FM_CORS_ALLOW_ORIGINS") or "").split(","):
        origin = raw_origin.strip().rstrip("/")
        if origin:
            origins.add(origin)

    return sorted(origins)


def create_app() -> FastAPI:
    container = build_container()
    app = FastAPI(
        title="Functional Medicine Nutrition AI",
        version="0.1.0",
        summary="Grounded recommendation engine for internal nutrition experts.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.container = container
    app.include_router(router)
    return app


app = create_app()
