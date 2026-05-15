from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.core.bootstrap import build_container


def create_app() -> FastAPI:
    app = FastAPI(
        title="Functional Medicine Nutrition AI",
        version="0.1.0",
        summary="Grounded recommendation engine for internal nutrition experts.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:3001",
            "http://127.0.0.1:3001",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.container = build_container()
    app.include_router(router)
    return app


app = create_app()
