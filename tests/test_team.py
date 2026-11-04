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


def test_upsert_remote_prompt_and_entity_with_assets(tmp_path):
    lib = TeamLibrary(data_dir=tmp_path / "upsert_test")

    # 1. Upsert prompt with image metadata
    pid = lib.upsert_remote_prompt({
        "title": "Neon Samurai",
        "platform": "flux",
        "prompt": "futuristic samurai in Tokyo neon rain",
        "negative_prompt": "blurry, low quality",
        "tags": ["samurai", "cyberpunk"],
        "variables": {},
        "has_image": 1,
        "image_hash": "deadbeef12345678",
        "model": "Flux 1.0",
        "sampler": "Euler a",
        "cfg_scale": 7.5,
        "steps": 30,
        "seed": 999888,
    }, gdrive_file_id="g_file_001")
    assert pid is not None

    p = lib.get_prompt_dict(pid)
    assert p["title"] == "Neon Samurai"
    assert p["platform"] == "flux"
    assert p["tags"] == ["samurai", "cyberpunk"]
    assert p["has_image"] == 1
    assert p["image_hash"] == "deadbeef12345678"
    assert p["model"] == "Flux 1.0"
    assert p["cfg_scale"] == 7.5
    assert p["seed"] == 999888
    assert p["gdrive_file_id"] == "g_file_001"

    # 2. Upsert entity with instructions and tags
    eid = lib.upsert_remote_entity({
        "title": "Marketing Copywriter",
        "platform": "claude",
        "entity_type": "skill",
        "system_instructions": "You write converting marketing copy.",
        "template": "Write an ad for {{product}}",
        "variables": ["product"],
        "tags": ["copywriting", "ads"],
    }, gdrive_file_id="g_entity_002")
    assert eid is not None

    e = lib.get_system_entity(eid)
    assert e.title == "Marketing Copywriter"
    assert e.variables == ["product"]
    assert e.tags == ["copywriting", "ads"]
    assert e.gdrive_file_id == "g_entity_002"


def test_gdrive_image_sync_and_pull_simulation(tmp_path):
    import json
    from unittest.mock import MagicMock
    from promptvault_team.gdrive_service import CorporateGDriveSync

    # 1. Setup PC 1 Library with an image prompt
    pc1_dir = tmp_path / "pc1"
    lib1 = TeamLibrary(data_dir=pc1_dir)

    dummy_png = _create_dummy_png_bytes(120, 120, "red")
    prompt_dict = lib1.add_prompt_with_asset(
        title="Red Sunset AI",
        prompt="beautiful sunset over ocean, anime style",
        platform="midjourney",
        image_bytes=dummy_png,
        tags=["sunset", "anime"],
    )
    assert prompt_dict["has_image"] == 1
    img_hash = prompt_dict["image_hash"]

    # 2. Mock Google Drive storage (in-memory dict of files and folders)
    gdrive_files = {}

    class MockDriveFilesResource:
        def list(self, **kwargs):
            q = kwargs.get("q", "")
            matched = []
            for fid, f in gdrive_files.items():
                if f.get("trashed"):
                    continue
                parts = [p.strip() for p in q.split(" and ")] if q else []
                match = True
                for part in parts:
                    if " in parents" in part:
                        pid = part.split(" in parents")[0].strip().strip("'\"")
                        if pid not in f.get("parents", []):
                            match = False
                            break
                    elif "name = " in part:
                        name_val = part.split("name = ")[1].strip().strip("'\"")
                        if f.get("name") != name_val:
                            match = False
                            break
                    elif "mimeType = " in part:
                        mime_val = part.split("mimeType = ")[1].strip().strip("'\"")
                        if f.get("mimeType") != mime_val:
                            match = False
                            break
                if match:
                    matched.append({"id": fid, "name": f["name"]})
            req = MagicMock()
            req.execute.return_value = {"files": matched}
            return req

        def create(self, body, media_body=None, **kwargs):
            fid = f"drive_id_{len(gdrive_files) + 1}"
            content = b""
            if media_body:
                if hasattr(media_body, "_fd") and hasattr(media_body._fd, "read"):
                    media_body._fd.seek(0)
                    content = media_body._fd.read()
                elif hasattr(media_body, "getblob"):
                    content = media_body.getblob()
            gdrive_files[fid] = {
                "id": fid,
                "name": body.get("name"),
                "mimeType": body.get("mimeType"),
                "parents": body.get("parents", []),
                "content": content,
                "trashed": False,
            }
            req = MagicMock()
            req.execute.return_value = {"id": fid}
            return req

        def update(self, fileId, media_body=None, **kwargs):
            if fileId in gdrive_files and media_body:
                if hasattr(media_body, "getblob"):
                    gdrive_files[fileId]["content"] = media_body.getblob()
            req = MagicMock()
            req.execute.return_value = {"id": fileId}
            return req

        def get_media(self, fileId, **kwargs):
            req = MagicMock()
            req.execute.return_value = gdrive_files[fileId]["content"]
            return req

    mock_service = MagicMock()
    mock_service.files.return_value = MockDriveFilesResource()

    # 3. Process sync item on PC 1
    sync1 = CorporateGDriveSync(lib1)
    sync1.build_service = MagicMock(return_value=mock_service)
    sync1._process_sync_item("prompt", prompt_dict["id"], "upsert")

    # Verify both the image file and prompt JSON were created in mock Google Drive!
    filenames = [f["name"] for f in gdrive_files.values()]
    assert f"{img_hash}.png" in filenames
    assert any(fn.startswith("prompt_") and fn.endswith(".json") for fn in filenames)

    # 4. Now simulate PC 2 pulling from Google Drive
    pc2_dir = tmp_path / "pc2"
    lib2 = TeamLibrary(data_dir=pc2_dir)
    sync2 = CorporateGDriveSync(lib2)
    sync2.build_service = MagicMock(return_value=mock_service)

    res_pull = sync2.pull_from_drive()
    assert res_pull["ok"] is True
    assert res_pull["synced_prompts"] >= 1
    assert res_pull["synced_images"] >= 1

    # Verify PC 2 now has the image file on local disk and prompt in SQLite!
    pc2_img_file = lib2.vault_media_dir / f"{img_hash}.png"
    assert pc2_img_file.exists()
    assert pc2_img_file.read_bytes() == dummy_png

    pc2_thumb_file = lib2.vault_thumbs_dir / f"thumb_{img_hash}.jpg"
    assert pc2_thumb_file.exists()

    pc2_prompts = lib2.list_prompts_dict()
    assert len(pc2_prompts) == 1
    assert pc2_prompts[0]["title"] == "Red Sunset AI"
    assert pc2_prompts[0]["has_image"] == 1
    assert pc2_prompts[0]["image_hash"] == img_hash
    assert pc2_prompts[0]["image_vault_path"] == str(pc2_img_file)
    assert pc2_prompts[0]["thumb_vault_path"] == str(pc2_thumb_file)


def test_app_updater_and_system_endpoints(tmp_path):
    from unittest.mock import patch, MagicMock
    from promptvault_team.updater import AppUpdater
    from promptvault_team.app import create_team_app
    from fastapi.testclient import TestClient

    updater = AppUpdater(repo_dir=tmp_path)
    # Since tmp_path is not a git repo:
    assert updater.is_git_repo() is False
    ver = updater.get_local_commit()
    assert "commit" in ver

    # Mock git repo and check_for_updates
    (tmp_path / ".git").mkdir()
    assert updater.is_git_repo() is True

    with patch.object(updater, "get_local_commit", return_value={"commit": "abc1234", "message": "Initial", "date": "today"}):
        with patch("subprocess.run") as mock_sub:
            # Mock git rev-parse origin/main returning a new hash
            mock_sub.return_value = MagicMock(returncode=0, stdout="def5678\n")
            res = updater.check_for_updates(force=True)
            assert res["local_commit"] == "abc1234"
            assert res["remote_commit"] == "def5678"
            assert res["has_update"] is True

    # Test FastAPI endpoints
    app = create_team_app(data_dir=tmp_path / "data", mode="admin", auto_close=False)
    client = TestClient(app)

    res_ver = client.get("/api/system/version")
    assert res_ver.status_code == 200
    assert "commit" in res_ver.json()

    res_chk = client.get("/api/system/check-update")
    assert res_chk.status_code == 200
    assert "has_update" in res_chk.json()


def test_push_to_drive_queuing(tmp_path):
    from promptvault_team.gdrive_service import CorporateGDriveSync

    lib = TeamLibrary(data_dir=tmp_path)
    lib.add_prompt(title="Test P1", prompt="hello world", platform="generic")
    lib.create_system_entity(
        title="Test Gem",
        platform="gemini",
        entity_type="gem",
        system_instructions="You are helpful.",
    )

    sync = CorporateGDriveSync(lib=lib)
    # Mock is_configured returning True
    sync.is_configured = lambda: True

    res = sync.push_to_drive()
    assert res["ok"] is True
    # At least 1 prompt + 1 entity + 1 policy = 3 queued
    assert res["queued"] >= 3
    assert sync._queue.qsize() >= 3


def test_updater_zip_fallback(tmp_path):
    import io
    import zipfile
    from unittest.mock import patch, MagicMock
    from promptvault_team.updater import AppUpdater

    target_dir = tmp_path / "app_dir"
    target_dir.mkdir()

    # Create dummy in-memory zip
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("promptvault-team-main/version_test.txt", "v2.1-success")
        zf.writestr("promptvault-team-main/promptvault_team/__init__.py", "# team")
    zip_bytes = buf.getvalue()

    updater = AppUpdater(repo_dir=target_dir)
    assert updater.is_git_repo() is False

    mock_resp = MagicMock()
    mock_resp.read.return_value = zip_bytes
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_cm), \
         patch("subprocess.run"), \
         patch("threading.Thread"):
        res = updater.apply_update()
        assert res["ok"] is True
        assert (target_dir / "version_test.txt").read_text() == "v2.1-success"
        assert (target_dir / ".version").exists()


def test_gdrive_dual_mode_and_token_persistence(tmp_path):
    from unittest.mock import MagicMock
    from promptvault_team.gdrive_service import CorporateGDriveSync

    lib = TeamLibrary(data_dir=tmp_path / "lib")
    sync = CorporateGDriveSync(lib=lib, config_dir=tmp_path / "cfg")

    # 1. Test token persistence in config
    test_token = "PVTEAM_eyJzaGFyZWRfZHJpdmVfaWQiOiAiT1JUQUtfMTIzIn0="
    sync.save_config(shared_drive_id="ORTAK_123", root_folder_name="Test Vault", last_token=test_token)
    cfg = sync.load_config()
    assert cfg["shared_drive_id"] == "ORTAK_123"
    assert cfg["root_folder_name"] == "Test Vault"
    assert cfg["last_token"] == test_token

    # 2. Test dual mode: Shared Drive vs Regular Folder
    mock_service = MagicMock()
    sync.build_service = lambda: mock_service
    sync.is_configured = lambda: True

    # Case A: Shared Drive returns successfully from drives().get()
    mock_service.drives().get().execute.return_value = {"id": "ORTAK_123", "name": "CT_AI_ORTAK"}
    assert sync._test_connection() is True
    assert sync._stats["drive_type"] == "shared_drive"
    assert sync._stats["shared_drive_name"] == "CT_AI_ORTAK"

    # Case B: Not a Shared Drive (drives().get throws 404), but is a Normal Folder (files().get succeeds)
    mock_service.drives().get().execute.side_effect = Exception("404 File not found")
    mock_service.files().get().execute.return_value = {
        "id": "FOLDER_456",
        "name": "Klasörüm",
        "mimeType": "application/vnd.google-apps.folder"
    }
    sync.save_config(shared_drive_id="FOLDER_456")
    assert sync._test_connection() is True
    assert sync._stats["drive_type"] == "folder"
    assert sync._stats["shared_drive_name"] == "Klasörüm"

    # Case C: Neither Shared Drive nor Folder accessible -> returns False with clear last_error
    mock_service.files().get().execute.side_effect = Exception("404 Not Found")
    assert sync._test_connection() is False
    assert "404 Not Found" in sync._stats["last_error"]
    assert "Çözüm Adımları" in sync._stats["last_error"]





