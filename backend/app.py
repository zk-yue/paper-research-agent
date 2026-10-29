"""FastAPI assembly and lifecycle. Importing this module performs no data writes."""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.routes import libraries, model, papers, reviews, tasks, websocket
from backend.task_manager import task_manager
from settings import ServerSettings
from storage import InvalidIdentifier


@asynccontextmanager
async def lifespan(app: FastAPI):
    task_manager.load_existing_tasks()
    from backend.review_service import recover_reviews, shutdown_reviews

    recover_reviews()
    try:
        yield
    finally:
        await shutdown_reviews()
        await task_manager.shutdown()


def create_app(settings: ServerSettings | None = None) -> FastAPI:
    settings = settings or ServerSettings.from_env()
    app = FastAPI(title="Paper Research Agent API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type"],
    )

    @app.exception_handler(InvalidIdentifier)
    async def invalid_identifier(request: Request, error: InvalidIdentifier):
        return JSONResponse(status_code=400, content={"detail": str(error)})

    @app.get("/api/health", tags=["health"])
    async def health():
        return {"status": "ok"}

    @app.get("/api/runtime", tags=["health"])
    async def runtime_info():
        provider = os.getenv("LLM_PROVIDER", "openai").strip().lower()
        return {
            "provider": provider,
            "model": os.getenv("CODEX_MODEL") or "CLI 默认模型"
            if provider == "codex"
            else os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            "incremental_output": provider != "codex",
        }

    for module in (tasks, papers, reviews, libraries, model, websocket):
        app.include_router(module.router)

    dist = settings.frontend_dist
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    if (dist / "index.html").is_file():

        @app.get("/{full_path:path}", include_in_schema=False)
        async def serve_frontend(full_path: str):
            if full_path == "api" or full_path.startswith(("api/", "ws/", "assets/")):
                raise HTTPException(status_code=404, detail="Not found")
            candidate = (dist / full_path).resolve()
            if candidate.is_relative_to(dist.resolve()) and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")

    return app
