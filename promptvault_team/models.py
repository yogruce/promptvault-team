"""Data models for PromptVault Team Edition.

Focuses on:
- Major LLM Platforms (ChatGPT, Gemini, Claude, Reasoning, OpenSource) without hardcoded version locks
- Advanced System Personas: Gemini Gems, ChatGPT GPTs, Claude Skills / Projects
- Workspaces, visibility controls, and real-time sync events
- Groundwork for upcoming API execution (Chat & Image generation)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class LLMPlatform(str, Enum):
    CHATGPT = "chatgpt"
    GEMINI = "gemini"
    CLAUDE = "claude"
    REASONING = "reasoning"
    OPEN_SOURCE = "opensource"
    OTHER = "other"


class SystemEntityType(str, Enum):
    GEM = "gem"                # Google Gemini Gem
    CUSTOM_GPT = "custom_gpt"  # OpenAI ChatGPT Custom GPT
    CLAUDE_SKILL = "skill"     # Anthropic Claude Project / Skill
    SYSTEM_PROMPT = "system"   # General System Instructions
    PROMPT_CHAIN = "chain"     # Sequential multi-step prompt


class PromptType(str, Enum):
    CHAT_TEXT = "chat_text"    # Conversations, code, copy, analysis
    IMAGE_GEN = "image_gen"    # DALL-E, Imagen, etc.
    MULTIMODAL = "multimodal"  # Vision + Text prompt


class Visibility(str, Enum):
    PRIVATE = "private"        # Only author
    TEAM = "team"              # Specific workspace / team
    COMPANY = "company"        # Entire organization


@dataclass
class Workspace:
    id: int
    name: str
    slug: str
    description: str = ""
    icon: str = "👥"
    color: str = "#4f46e5"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class SystemEntity:
    """Represents a Gem, Custom GPT, Claude Skill, or System Persona."""
    id: int
    title: str
    platform: str                    # LLMPlatform or custom
    entity_type: str                 # SystemEntityType: gem, custom_gpt, skill, etc.
    system_instructions: str         # The master instructions / persona prompt
    template: str = ""               # Optional template with {{variables}}
    variables: list[str] = field(default_factory=list)  # e.g. ["topic", "target_audience"]
    description: str = ""
    tags: list[str] = field(default_factory=list)
    workspace_id: int | None = None
    author: str = "Admin"
    visibility: str = Visibility.TEAM.value
    # Future API execution defaults
    model_hint: str = ""             # Dynamic, free-form (e.g. gemini-flash, gpt-next)
    temperature: float = 0.7
    max_tokens: int | None = None
    extra_params: dict[str, Any] = field(default_factory=dict)
    # Sync metadata
    gdrive_file_id: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class PromptItem:
    """A prompt entry in the team vault (chat text, code, or image prompt)."""
    id: int
    title: str
    prompt: str
    negative_prompt: str = ""
    prompt_type: str = PromptType.CHAT_TEXT.value
    platform: str = LLMPlatform.CHATGPT.value
    system_entity_id: int | None = None  # Linked Gem/GPT/Skill if applicable
    variables: dict[str, str] = field(default_factory=dict)  # Variable values if templated
    tags: list[str] = field(default_factory=list)
    author: str = "Member"
    workspace_id: int | None = None
    visibility: str = Visibility.TEAM.value
    rating: int = 0                  # 0 to 5 stars
    favorite: bool = False
    notes: str = ""
    source_url: str = ""
    # Optional image asset association
    image_hash: str | None = None
    image_vault_path: str | None = None
    # Sync metadata
    gdrive_file_id: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class ExecutionRequest:
    """Groundwork for future API execution (Chat & Image generation)."""
    platform: str                    # chatgpt, gemini, claude, etc.
    model_name: str                  # Free-form user/team chosen model
    mode: str = "chat"               # "chat" or "image"
    system_entity_id: int | None = None
    system_prompt: str = ""
    user_prompt: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    api_key_override: str | None = None


@dataclass
class ExecutionResult:
    """Groundwork for execution response from APIs."""
    success: bool
    output_text: str = ""
    output_images: list[str] = field(default_factory=list)  # URLs or base64
    usage_tokens: dict[str, int] = field(default_factory=dict)
    latency_ms: float = 0.0
    error_message: str | None = None
    raw_response: dict[str, Any] = field(default_factory=dict)
