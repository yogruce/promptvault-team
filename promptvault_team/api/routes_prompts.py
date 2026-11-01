"""FastAPI endpoints for Team Prompts & Visual Vault."""
from __future__ import annotations

from pathlib import Path
from fastapi import APIRouter, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..db import TeamLibrary
from ..models import LLMPlatform, PromptType, Visibility


class PromptCreateRequest(BaseModel):
    title: str
    prompt: str
    negative_prompt: str = ""
    prompt_type: str = PromptType.CHAT_TEXT.value
    platform: str = LLMPlatform.CHATGPT.value
    system_entity_id: int | None = None
    tags: list[str] = []
    author: str = "Member"
    workspace_id: int | None = None
    visibility: str = Visibility.TEAM.value
    notes: str = ""
    source_url: str = ""


def create_prompts_router(lib: TeamLibrary) -> APIRouter:
    router = APIRouter(prefix="/api/team/prompts", tags=["team-prompts"])

    @router.get("")
    def list_prompts(
        platform: str | None = None,
        prompt_type: str | None = None,
        q: str | None = None,
        limit: int = Query(100, le=500),
    ):
        items = lib.list_prompts_dict(
            platform=platform,
            prompt_type=prompt_type,
            search_query=q,
            limit=limit,
        )
        return {"items": items, "total": len(items)}

    @router.post("")
    def add_prompt(req: PromptCreateRequest, authorization: str | None = Header(None)):
        token = (authorization or "").replace("Bearer ", "").strip()
        user = lib.get_user_by_token(token) if token else None
        author_name = user["name"] if user else req.author
        author_id = user["id"] if user else None

        if not req.prompt.strip():
            raise HTTPException(400, "Prompt içeriği boş olamaz.")

        item = lib.add_prompt_with_asset(
            title=req.title,
            prompt=req.prompt,
            negative_prompt=req.negative_prompt,
            prompt_type=req.prompt_type,
            platform=req.platform,
            system_entity_id=req.system_entity_id,
            tags=req.tags,
            author=author_name,
            author_id=author_id,
            workspace_id=req.workspace_id,
            visibility=req.visibility,
            notes=req.notes,
            source_url=req.source_url,
        )
        return {"ok": True, "prompt": item}

    @router.post("/upload-asset")
    async def upload_asset(
        file: UploadFile = File(...),
        title: str = Form(""),
        prompt: str = Form(""),
        negative_prompt: str = Form(""),
        platform: str = Form("chatgpt"),
        prompt_type: str = Form(PromptType.IMAGE_GEN.value),
        tags: str = Form(""),
        authorization: str | None = Header(None),
    ):
        token = (authorization or "").replace("Bearer ", "").strip()
        user = lib.get_user_by_token(token) if token else None
        author_name = user["name"] if user else "Member"
        author_id = user["id"] if user else None

        img_bytes = await file.read()
        if not img_bytes:
            raise HTTPException(400, "Boş dosya yüklendi.")

        tag_list = [t.strip() for t in tags.split(",") if t.strip()]

        item = lib.add_prompt_with_asset(
            title=title or file.filename or "Yüklenen Görsel",
            prompt=prompt,
            negative_prompt=negative_prompt,
            prompt_type=prompt_type,
            platform=platform,
            tags=tag_list,
            author=author_name,
            author_id=author_id,
            image_bytes=img_bytes,
        )
        return {"ok": True, "prompt": item}

    @router.get("/{prompt_id}/image")
    def get_prompt_image(prompt_id: int):
        item = lib.get_prompt_dict(prompt_id)
        if not item or not item.get("image_vault_path"):
            raise HTTPException(404, "Görsel bulunamadı.")
        path = Path(item["image_vault_path"])
        if not path.exists():
            raise HTTPException(404, "Görsel dosyası diskte bulunamadı.")
        return FileResponse(str(path))

    @router.get("/{prompt_id}/thumb")
    def get_prompt_thumb(prompt_id: int):
        item = lib.get_prompt_dict(prompt_id)
        if not item or not item.get("thumb_vault_path"):
            raise HTTPException(404, "Küçük resim bulunamadı.")
        path = Path(item["thumb_vault_path"])
        if not path.exists():
            return get_prompt_image(prompt_id)
        return FileResponse(str(path))

    @router.get("/{prompt_id}")
    def get_prompt(prompt_id: int):
        item = lib.get_prompt_dict(prompt_id)
        if not item:
            raise HTTPException(404, "Prompt bulunamadı.")
        return item

    return router
