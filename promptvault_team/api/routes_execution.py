"""FastAPI endpoints & groundwork for future API execution (Chat & Image generation).

Allows testing prompt templates with variable substitutions, and lays the
scaffolding for upcoming direct OpenAI / Gemini / Anthropic API calls.
"""
from __future__ import annotations

import re
from typing import Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..db import TeamLibrary
from ..models import ExecutionRequest, ExecutionResult


class RenderTemplateRequest(BaseModel):
    template: str
    variables: dict[str, str] = {}


class APIExecutePreviewRequest(BaseModel):
    platform: str = "chatgpt"        # chatgpt, gemini, claude, reasoning
    model_name: str = ""             # Dynamic model name (free-form)
    mode: str = "chat"               # "chat" or "image"
    system_entity_id: int | None = None
    system_instructions: str = ""
    prompt: str = ""
    variables: dict[str, str] = {}
    parameters: dict[str, Any] = {}


def render_template(template: str, variables: dict[str, str]) -> str:
    """Substitutes {{variable}} placeholders with values."""
    if not template:
        return ""
    result = template
    for key, val in variables.items():
        pattern = re.compile(rf"\{{\{{\s*{re.escape(key)}\s*\}}\}}")
        result = pattern.sub(val, result)
    return result


def create_execution_router(lib: TeamLibrary) -> APIRouter:
    router = APIRouter(prefix="/api/team/execute", tags=["team-execute"])

    @router.post("/render")
    def render(req: RenderTemplateRequest):
        rendered = render_template(req.template, req.variables)
        return {"rendered": rendered}

    @router.post("/preview")
    def preview_execution(req: APIExecutePreviewRequest):
        """Prepares the assembled payload (System prompt + Rendered prompt + Model params)

        ready for upcoming API provider drivers.
        """
        sys_prompt = req.system_instructions
        if req.system_entity_id:
            entity = lib.get_system_entity(req.system_entity_id)
            if entity:
                sys_prompt = entity.system_instructions
                if not req.model_name and entity.model_hint:
                    req.model_name = entity.model_hint

        rendered_prompt = render_template(req.prompt, req.variables)

        # Groundwork payload for upcoming phase (OpenAI / Gemini / Anthropic API drivers)
        assembled_payload = {
            "platform": req.platform,
            "model_name": req.model_name or "default",
            "mode": req.mode,
            "messages": [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": rendered_prompt},
            ] if req.mode == "chat" else None,
            "image_prompt": rendered_prompt if req.mode == "image" else None,
            "parameters": req.parameters,
            "ready_for_dispatch": True,
        }

        return {
            "status": "ready",
            "assembled_payload": assembled_payload,
            "message": "API motoru için hazırlandı. Sonraki fazda doğrudan API anahtarlarıyla tetiklenebilir.",
        }

    return router
