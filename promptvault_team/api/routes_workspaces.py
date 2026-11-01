"""FastAPI endpoints for Team Workspaces."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..db import TeamLibrary


class WorkspaceCreateRequest(BaseModel):
    name: str
    slug: str | None = None
    description: str = ""
    icon: str = "👥"
    color: str = "#4f46e5"


def create_workspaces_router(lib: TeamLibrary) -> APIRouter:
    router = APIRouter(prefix="/api/team/workspaces", tags=["team-workspaces"])

    @router.get("")
    def list_workspaces():
        items = lib.list_workspaces()
        return {"items": [item.__dict__ for item in items]}

    @router.post("")
    def create_workspace(req: WorkspaceCreateRequest):
        if not req.name.strip():
            raise HTTPException(400, "Çalışma alanı adı boş olamaz.")
        ws = lib.create_workspace(
            name=req.name,
            slug=req.slug,
            description=req.description,
            icon=req.icon,
            color=req.color,
        )
        return {"ok": True, "workspace": ws.__dict__}

    return router
