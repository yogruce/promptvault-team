"""FastAPI main application for PromptVault Team Edition with Auth & Visual Vault."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import (
    create_auth_router,
    create_entities_router,
    create_execution_router,
    create_gdrive_router,
    create_prompts_router,
    create_workspaces_router,
)
from .db import TeamLibrary
from .gdrive_service import CorporateGDriveSync


def create_team_app(data_dir: str | Path | None = None, mode: str = "admin") -> FastAPI:
    lib = TeamLibrary(data_dir=data_dir, mode=mode)
    gdrive_sync = CorporateGDriveSync(lib=lib)
    static_dir = Path(__file__).resolve().parent / "static"

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        gdrive_sync.stop_worker()

    app = FastAPI(
        title="PromptVault Team API",
        version="2.0.0-team",
        description="Team-wide AI Prompt & Visual Vault with Real Auth, ChatGPT, Gemini, Claude and Drive Sync.",
        lifespan=lifespan,
    )

    # Enable CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include routers
    app.include_router(create_auth_router(lib))
    app.include_router(create_entities_router(lib))
    app.include_router(create_prompts_router(lib))
    app.include_router(create_workspaces_router(lib))
    app.include_router(create_gdrive_router(gdrive_sync))
    app.include_router(create_execution_router(lib))

    # Mount static directory
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/", response_class=HTMLResponse)
    def root():
        index_file = static_dir / "index.html"
        if index_file.exists():
            return HTMLResponse(
                content=index_file.read_text(encoding="utf-8"),
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate, max-age=0",
                    "Pragma": "no-cache",
                    "Expires": "0",
                },
            )
        return HTMLResponse("<h2>PromptVault Team UI yüklenemedi (index.html bulunamadı)</h2>", status_code=500)

    @app.get("/client", response_class=HTMLResponse)
    @app.get("/admin", response_class=HTMLResponse)
    def web_aliases():
        return root()

    return app
