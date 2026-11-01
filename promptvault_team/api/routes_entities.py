"""FastAPI endpoints for System Entities (Gemini Gems, ChatGPT GPTs, Claude Skills)."""
from __future__ import annotations

from typing import Any
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..db import TeamLibrary
from ..models import LLMPlatform, SystemEntityType, Visibility


class EntityCreateRequest(BaseModel):
    title: str
    platform: str = LLMPlatform.GEMINI.value
    entity_type: str = SystemEntityType.GEM.value
    system_instructions: str
    template: str = ""
    description: str = ""
    tags: list[str] = []
    workspace_id: int | None = None
    author: str = "Admin"
    visibility: str = Visibility.TEAM.value
    model_hint: str = ""
    temperature: float = 0.7
    max_tokens: int | None = None
    extra_params: dict[str, Any] = {}


def create_entities_router(lib: TeamLibrary) -> APIRouter:
    router = APIRouter(prefix="/api/team/entities", tags=["team-entities"])

    @router.get("")
    def list_entities(
        platform: str | None = None,
        entity_type: str | None = None,
        workspace_id: int | None = None,
        q: str | None = None,
        limit: int = Query(100, le=500),
    ):
        items = lib.list_system_entities(
            platform=platform,
            entity_type=entity_type,
            workspace_id=workspace_id,
            search_query=q,
            limit=limit,
        )
        return {"items": [item.__dict__ for item in items], "total": len(items)}

    @router.post("")
    def create_entity(req: EntityCreateRequest):
        if not req.title.strip() or not req.system_instructions.strip():
            raise HTTPException(400, "Başlık (title) ve sistem yönergesi (system_instructions) zorunludur.")
        entity = lib.create_system_entity(
            title=req.title,
            platform=req.platform,
            entity_type=req.entity_type,
            system_instructions=req.system_instructions,
            template=req.template,
            description=req.description,
            tags=req.tags,
            workspace_id=req.workspace_id,
            author=req.author,
            visibility=req.visibility,
            model_hint=req.model_hint,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
            extra_params=req.extra_params,
        )
        return {"ok": True, "entity": entity.__dict__}

    @router.get("/{entity_id}")
    def get_entity(entity_id: int):
        entity = lib.get_system_entity(entity_id)
        if not entity:
            raise HTTPException(404, "Sistem varlığı (Gem/GPT/Skill) bulunamadı.")
        return entity.__dict__

    return router
