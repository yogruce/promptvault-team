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
    create_system_router,
    create_workspaces_router,
)
from .db import TeamLibrary
from .gdrive_service import CorporateGDriveSync
from .updater import AppUpdater


def create_team_app(
    data_dir: str | Path | None = None,
    mode: str = "admin",
    auto_close: bool = False,
) -> FastAPI:
    import os
    import time
    import threading

    lib = TeamLibrary(data_dir=data_dir, mode=mode)
    gdrive_sync = CorporateGDriveSync(lib=lib)
    updater = AppUpdater()
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

    # Browser auto-close watcher state
    app.state.last_heartbeat = time.time()
    app.state.has_connected = False
    app.state.shutdown_requested = False
    app.state.shutdown_time = 0.0

    def _auto_close_watcher():
        time.sleep(8)
        start_time = time.time()
        while True:
            time.sleep(1.0)
            now = time.time()
            if app.state.has_connected:
                elapsed = now - app.state.last_heartbeat
                if app.state.shutdown_requested and (now - app.state.shutdown_time > 4.0) and elapsed > 4.0:
                    print("\n[BİLGİ] Tarayıcı kapatıldı, terminal sonlandırılıyor...")
                    os._exit(0)
                elif elapsed > 12.0:
                    print("\n[BİLGİ] Tarayıcı bağlantısı kesildi, terminal sonlandırılıyor...")
                    os._exit(0)
            elif (now - start_time) > 60.0:
                print("\n[BİLGİ] Tarayıcı bağlantısı kurulmadı, terminal sonlandırılıyor...")
                os._exit(0)

    if auto_close:
        threading.Thread(target=_auto_close_watcher, daemon=True, name="AutoCloseWatcher").start()

    @app.post("/api/system/heartbeat")
    def heartbeat():
        app.state.last_heartbeat = time.time()
        app.state.has_connected = True
        app.state.shutdown_requested = False
        return {"status": "alive"}

    @app.post("/api/system/shutdown")
    def shutdown_signal():
        app.state.shutdown_requested = True
        app.state.shutdown_time = time.time()
        return {"status": "shutting_down"}

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
    app.include_router(create_system_router(updater))

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
