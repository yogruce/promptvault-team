"""Unit tests for PromptVault Team Edition."""
from __future__ import annotations

import io
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from promptvault_team.app import create_team_app
from promptvault_team.db import TeamLibrary, extract_template_variables
from promptvault_team.models import LLMPlatform, PromptType, SystemEntityType


def _create_dummy_png_bytes(width: int = 100, height: int = 100, color: str = "blue") -> bytes:
    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


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


def test_visual_vault_image_upload_and_thumb(tmp_path):
    app = create_team_app(data_dir=tmp_path / "img_vault")
    client = TestClient(app)

    # Create dummy PNG file
    png_bytes = _create_dummy_png_bytes(250, 250, "purple")

    files = {"file": ("test_render.png", png_bytes, "image/png")}
    data = {
        "title": "Cyberpunk Neon City",
        "prompt": "hyperrealistic futuristic cyberpunk city at night with neon lights",
        "platform": "midjourney",
        "tags": "cyberpunk, scifi, neon",
    }

    resp = client.post("/api/team/prompts/upload-asset", files=files, data=data)
    assert resp.status_code == 200
    item = resp.json()["prompt"]
    prompt_id = item["id"]
    assert item["has_image"] == 1
    assert item["image_hash"] is not None

    # Retrieve prompt details
    r_item = client.get(f"/api/team/prompts/{prompt_id}")
    assert r_item.status_code == 200
    assert r_item.json()["title"] == "Cyberpunk Neon City"

    # Retrieve full image
    r_img = client.get(f"/api/team/prompts/{prompt_id}/image")
    assert r_img.status_code == 200
    assert r_img.headers["content-type"].startswith("image/")

    # Retrieve thumbnail
    r_thumb = client.get(f"/api/team/prompts/{prompt_id}/thumb")
    assert r_thumb.status_code == 200
    assert r_thumb.headers["content-type"].startswith("image/")

    # Filter prompts by platform
    r_list = client.get("/api/team/prompts?platform=midjourney")
    assert r_list.status_code == 200
    assert len(r_list.json()["items"]) == 1


def test_gdrive_config_and_connect_token_roundtrip(tmp_path):
    # Admin server generates connect token
    admin_app = create_team_app(data_dir=tmp_path / "drive_admin", mode="admin")
    admin_client = TestClient(admin_app)

    # Save Drive configuration
    cfg_resp = admin_client.post(
        "/api/team/gdrive/config",
        json={"shared_drive_id": "0ABcDeF12345678", "root_folder_name": "Acme Team Vault"},
    )
    assert cfg_resp.status_code == 200

    # Get Connect Token
    t_resp = admin_client.get("/api/team/gdrive/connect-token")
    assert t_resp.status_code == 200
    token = t_resp.json()["token"]
    assert token.startswith("PVTEAM_")

    # Client server connects using token
    client_app = create_team_app(data_dir=tmp_path / "drive_client", mode="client")
    client_client = TestClient(client_app)

    apply_resp = client_client.post(
        "/api/team/gdrive/connect-token",
        json={"token": token},
    )
    assert apply_resp.status_code == 200
    assert apply_resp.json()["ok"] is True

    # Verify client config matches
    client_cfg = client_client.get("/api/team/gdrive/config").json()
    assert client_cfg["shared_drive_id"] == "0ABcDeF12345678"
    assert client_cfg["root_folder_name"] == "Acme Team Vault"
