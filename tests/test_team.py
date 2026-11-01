"""Unit tests for PromptVault Team Edition."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from promptvault_team.app import create_team_app
from promptvault_team.db import TeamLibrary, extract_template_variables
from promptvault_team.models import LLMPlatform, PromptType, SystemEntityType


def test_extract_template_variables():
    tmpl = "Merhaba {{musteri_adi}}, bize {{sektor}} alanındaki hedeflerinizden bahseder misiniz? Tekrar: {{musteri_adi}}"
    vars_found = extract_template_variables(tmpl)
    assert vars_found == ["musteri_adi", "sektor"]


def test_team_library_basic(tmp_path):
    lib = TeamLibrary(data_dir=tmp_path)

    # 1. Check default workspace
    workspaces = lib.list_workspaces()
    assert len(workspaces) >= 1
    assert workspaces[0].slug == "default"

    # 2. Create custom workspace
    ws = lib.create_workspace("Pazarlama Ekibi", description="Pazarlama promptları")
    assert ws.id is not None
    assert ws.slug == "pazarlama-ekibi"

    # 3. Create System Entity (Gemini Gem / Claude Skill)
    sync_events = []
    lib.add_sync_listener(lambda t, i, a: sync_events.append((t, i, a)))

    gem = lib.create_system_entity(
        title="SEO Makale Uzmanı",
        platform=LLMPlatform.GEMINI.value,
        entity_type=SystemEntityType.GEM.value,
        system_instructions="Sen profesyonel bir SEO ve içerik stratejistisin.",
        template="{{anahtar_kelime}} hakkında {{hedef_kitle}} için 1000 kelimelik makale yaz.",
        tags=["seo", "icerik", "blog"],
        workspace_id=ws.id,
    )
    assert gem.id is not None
    assert gem.variables == ["anahtar_kelime", "hedef_kitle"]
    assert len(sync_events) == 1
    assert sync_events[0] == ("system_entity", gem.id, "upsert")

    # 4. Search System Entities via FTS
    results = lib.list_system_entities(search_query="içerik")
    assert len(results) >= 1
    assert results[0].title == "SEO Makale Uzmanı"

    # 5. Add Prompt
    p = lib.add_prompt(
        title="Lüks Kahve Kampanyası",
        prompt="Gurme kahve severler için Instagram kampanya metni hazırla.",
        platform=LLMPlatform.CHATGPT.value,
        system_entity_id=gem.id,
        tags=["kahve", "pazarlama"],
        workspace_id=ws.id,
    )
    assert p.id is not None
    assert len(sync_events) == 2
    assert sync_events[1] == ("prompt", p.id, "upsert")

    # 6. Search Prompts
    prompts = lib.list_prompts(search_query="kampanya")
    assert len(prompts) >= 1
    assert prompts[0].id == p.id


def test_team_api_endpoints(tmp_path):
    app = create_team_app(data_dir=tmp_path)
    client = TestClient(app)

    # Root (Serves HTML Dashboard)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "PromptVault" in resp.text

    # Create workspace
    resp = client.post("/api/team/workspaces", json={"name": "Yazılım Ekibi"})
    assert resp.status_code == 200
    ws_id = resp.json()["workspace"]["id"]

    # Create Custom GPT
    resp = client.post(
        "/api/team/entities",
        json={
            "title": "Python Kod Denetçisi",
            "platform": "chatgpt",
            "entity_type": "custom_gpt",
            "system_instructions": "Sen kıdemli bir Python mimarısın. PEP8 ve clean code ilkelerine göre denetle.",
            "template": "{{kod}} için kod incelemesi yap.",
            "workspace_id": ws_id,
        },
    )
    assert resp.status_code == 200
    entity_id = resp.json()["entity"]["id"]

    # Preview execution payload (groundwork for upcoming API phase)
    resp = client.post(
        "/api/team/execute/preview",
        json={
            "platform": "chatgpt",
            "system_entity_id": entity_id,
            "prompt": "{{kod}} için kod incelemesi yap.",
            "variables": {"kod": "def topla(a, b): return a + b"},
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ready"
    assert "def topla(a, b): return a + b" in data["assembled_payload"]["messages"][1]["content"]

    # Check GDrive status endpoint
    resp = client.get("/api/team/gdrive/status")
    assert resp.status_code == 200
    assert "is_configured" in resp.json()
