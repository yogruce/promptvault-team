"""System and auto-update API endpoints for PromptVault Team."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from ..updater import AppUpdater


def create_system_router(updater: AppUpdater) -> APIRouter:
    router = APIRouter(prefix="/api/system", tags=["System"])

    @router.get("/version")
    def get_version():
        return updater.get_local_commit()

    @router.get("/check-update")
    def check_update(force: bool = False):
        return updater.check_for_updates(force=force)

    @router.post("/apply-update")
    def apply_update():
        try:
            return updater.apply_update()
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    return router
