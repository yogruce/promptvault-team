"""Corporate Google Drive Synchronization for PromptVault Team.

Features:
- Google Cloud Service Account authentication (ideal for headless team servers)
- Google Workspace Shared Drive (Ortak Drive) support (`supportsAllDrives=True`)
- Real-time Background Sync Worker: Vault'a eklendiği/güncellendiği anda Drive'a işler
- Fallback/Dual support for OAuth2 credentials
"""
from __future__ import annotations

import io
import json
import os
import queue
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import TeamLibrary

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.file",
]


class CorporateGDriveSync:
    """Manages Google Workspace Shared Drive synchronization in real-time."""

    def __init__(self, lib: TeamLibrary, config_dir: Path | None = None):
        self.lib = lib
        self.config_dir = (config_dir or lib.data_dir) / "gdrive_team"
        self.config_dir.mkdir(parents=True, exist_ok=True)

        self.service_account_file = self.config_dir / "service_account.json"
        self.config_file = self.config_dir / "team_drive_config.json"

        self._queue: queue.Queue[tuple[str, int, str]] = queue.Queue()
        self._worker_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        # Cached folder IDs in Google Drive
        self._folder_cache: dict[str, str] = {}

        # Last sync stats
        self._stats = {
            "is_connected": False,
            "shared_drive_id": "",
            "shared_drive_name": "",
            "synced_count": 0,
            "failed_count": 0,
            "last_synced_at": None,
            "last_error": None,
            "queue_pending": 0,
        }

        # Subscribe to library real-time changes
        self.lib.add_sync_listener(self._on_library_change)

        # Start background worker
        self._start_worker()

    def _on_library_change(self, item_type: str, item_id: int, action: str) -> None:
        """Called immediately when a prompt or entity is created/updated in the vault."""
        self._queue.put((item_type, item_id, action))
        self._stats["queue_pending"] = self._queue.qsize()

    # ------------------------------------------------------------- Configuration
    def load_config(self) -> dict[str, Any]:
        if not self.config_file.exists():
            return {"shared_drive_id": "", "root_folder_name": "PromptVault Team Vault"}
        try:
            return json.loads(self.config_file.read_text(encoding="utf-8"))
        except Exception:
            return {"shared_drive_id": "", "root_folder_name": "PromptVault Team Vault"}

    def save_config(self, shared_drive_id: str = "", root_folder_name: str = "PromptVault Team Vault") -> None:
        cfg = {
            "shared_drive_id": shared_drive_id.strip(),
            "root_folder_name": root_folder_name.strip() or "PromptVault Team Vault",
        }
        self.config_file.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        self._folder_cache.clear()

    def generate_connect_token(self) -> str:
        """Generates a base64 encoded setup token for remote team members."""
        import base64
        cfg = self.load_config()
        pol = self.lib.get_company_policy()
        sa_b64 = ""
        if self.service_account_file.exists():
            sa_b64 = base64.b64encode(self.service_account_file.read_bytes()).decode("utf-8")
        
        token_data = {
            "shared_drive_id": cfg.get("shared_drive_id", ""),
            "root_folder_name": cfg.get("root_folder_name", "PromptVault Team Vault"),
            "company_name": pol.get("company_name", "Şirket Kasası"),
            "sa": sa_b64,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        raw_json = json.dumps(token_data, ensure_ascii=False)
        return "PVTEAM_" + base64.urlsafe_b64encode(raw_json.encode("utf-8")).decode("utf-8")

    def apply_connect_token(self, token_str: str) -> dict[str, Any]:
        """Applies a base64 team connect token on a client machine."""
        import base64
        token_clean = token_str.strip()
        if token_clean.startswith("PVTEAM_"):
            token_clean = token_clean[len("PVTEAM_"):]
        try:
            raw_bytes = base64.urlsafe_b64decode(token_clean.encode("utf-8"))
            data = json.loads(raw_bytes.decode("utf-8"))
        except Exception as e:
            raise ValueError(f"Geçersiz Ekip Bağlantı Kodu: {e}")

        # Save config
        self.save_config(
            shared_drive_id=data.get("shared_drive_id", ""),
            root_folder_name=data.get("root_folder_name", "PromptVault Team Vault"),
        )
        # Save service account if provided
        sa_b64 = data.get("sa", "")
        if sa_b64:
            sa_bytes = base64.b64decode(sa_b64.encode("utf-8"))
            self.save_service_account_json(sa_bytes)

        # Pull everything immediately
        pull_result = {}
        if self.is_configured():
            try:
                pull_result = self.pull_from_drive()
            except Exception as e:
                pull_result = {"error": str(e)}

        return {
            "ok": True,
            "company_name": data.get("company_name", "Şirket Kasası"),
            "pull_result": pull_result,
            "status": self.get_status(),
        }

    def save_service_account_json(self, json_content: bytes | str) -> bool:
        if isinstance(json_content, str):
            json_content = json_content.encode("utf-8")
        try:
            # Validate JSON
            parsed = json.loads(json_content.decode("utf-8"))
            if "client_email" not in parsed or "private_key" not in parsed:
                raise ValueError("Geçersiz Service Account JSON dosyası (client_email veya private_key eksik)")
            self.service_account_file.write_bytes(json_content)
            self._test_connection()
            return True
        except Exception as e:
            self._stats["last_error"] = str(e)
            return False

    def is_configured(self) -> bool:
        return self.service_account_file.exists()

    def get_credentials(self):
        if not self.service_account_file.exists():
            return None
        from google.oauth2 import service_account
        return service_account.Credentials.from_service_account_file(
            str(self.service_account_file),
            scopes=SCOPES,
        )

    def build_service(self):
        creds = self.get_credentials()
        if not creds:
            raise RuntimeError("Google Service Account anahtarı bulunamadı")
        from googleapiclient.discovery import build
        return build("drive", "v3", credentials=creds)

    def _test_connection(self) -> bool:
        try:
            service = self.build_service()
            cfg = self.load_config()
            shared_drive_id = cfg.get("shared_drive_id")
            drive_name = "My Drive / Service Account Space"
            if shared_drive_id:
                try:
                    drive_info = service.drives().get(driveId=shared_drive_id).execute()
                    drive_name = drive_info.get("name", shared_drive_id)
                except Exception:
                    pass
            self._stats["is_connected"] = True
            self._stats["shared_drive_id"] = shared_drive_id or ""
            self._stats["shared_drive_name"] = drive_name
            self._stats["last_error"] = None
            return True
        except Exception as e:
            self._stats["is_connected"] = False
            self._stats["last_error"] = str(e)
            return False

    def get_status(self) -> dict[str, Any]:
        self._stats["queue_pending"] = self._queue.qsize()
        self._stats["is_configured"] = self.is_configured()
        return dict(self._stats)

    # ------------------------------------------------------------- Drive Folder Management
    def _get_or_create_folder(self, service, folder_name: str, parent_id: str | None = None) -> str:
        cache_key = f"{parent_id}:{folder_name}"
        if cache_key in self._folder_cache:
            return self._folder_cache[cache_key]

        cfg = self.load_config()
        shared_drive_id = cfg.get("shared_drive_id")

        q = f"name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        if parent_id:
            q += f" and '{parent_id}' in parents"
        elif shared_drive_id:
            q += f" and '{shared_drive_id}' in parents"

        kwargs: dict[str, Any] = {
            "q": q,
            "fields": "files(id, name)",
            "supportsAllDrives": True,
            "includeItemsFromAllDrives": True,
        }
        if shared_drive_id:
            kwargs["corpora"] = "drive"
            kwargs["driveId"] = shared_drive_id

        res = service.files().list(**kwargs).execute()
        files = res.get("files", [])
        if files:
            fid = files[0]["id"]
            self._folder_cache[cache_key] = fid
            return fid

        # Create folder
        meta: dict[str, Any] = {
            "name": folder_name,
            "mimeType": "application/vnd.google-apps.folder",
        }
        parents = []
        if parent_id:
            parents.append(parent_id)
        elif shared_drive_id:
            parents.append(shared_drive_id)
        if parents:
            meta["parents"] = parents

        created = service.files().create(body=meta, fields="id", supportsAllDrives=True).execute()
        fid = created["id"]
        self._folder_cache[cache_key] = fid
        return fid

    # ------------------------------------------------------------- Real-time Worker
    def _start_worker(self) -> None:
        if self._worker_thread and self._worker_thread.is_alive():
            return
        self._stop_event.clear()
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True, name="PromptVaultTeam-Sync")
        self._worker_thread.start()

    def stop_worker(self) -> None:
        self._stop_event.set()
        self._queue.put(("__stop__", 0, ""))

    def _worker_loop(self) -> None:
        last_pull_time = time.time()
        while not self._stop_event.is_set():
            try:
                item_type, item_id, action = self._queue.get(timeout=2.0)
                if item_type == "__stop__":
                    break

                if not self.is_configured():
                    self._queue.task_done()
                    continue

                try:
                    self._process_sync_item(item_type, item_id, action)
                    self._stats["synced_count"] += 1
                    self._stats["last_synced_at"] = datetime.now(timezone.utc).isoformat()
                    self._stats["last_error"] = None
                except Exception as e:
                    self._stats["failed_count"] += 1
                    self._stats["last_error"] = str(e)
                finally:
                    self._queue.task_done()
                    self._stats["queue_pending"] = self._queue.qsize()

            except queue.Empty:
                pass

            # Background auto-pull from Google Drive every 30 seconds if configured
            now = time.time()
            if (now - last_pull_time) >= 30.0 and self.is_configured() and self._queue.empty():
                last_pull_time = now
                try:
                    self.pull_from_drive()
                except Exception as e:
                    self._stats["last_error"] = f"Arka plan senkronizasyon hatası: {e}"

    def push_to_drive(self) -> dict[str, Any]:
        """Pushes all local prompts, entities, and policy to Google Drive."""
        if not self.is_configured():
            raise ValueError("Google Drive henüz yapılandırılmamış.")

        prompts = self.lib.list_prompts_dict(limit=1000)
        entities = self.lib.list_system_entities_dict()
        queued = 0

        for p in prompts:
            self._queue.put(("prompt", p["id"], "upsert"))
            queued += 1

        for e in entities:
            self._queue.put(("system_entity", e["id"], "upsert"))
            queued += 1

        self._queue.put(("company_policy", 1, "update"))
        queued += 1

        self._stats["queue_pending"] = self._queue.qsize()
        return {"ok": True, "queued": queued}

    def _process_sync_item(self, item_type: str, item_id: int, action: str) -> None:
        service = self.build_service()
        cfg = self.load_config()
        root_name = cfg.get("root_folder_name", "PromptVault Team Vault")

        root_id = self._get_or_create_folder(service, root_name)
        from googleapiclient.http import MediaFileUpload, MediaInMemoryUpload

        if item_type == "prompt":
            p_dict = self.lib.get_prompt_dict(item_id)
            if not p_dict:
                return
            prompts_folder_id = self._get_or_create_folder(service, "Prompts", parent_id=root_id)

            has_image = bool(p_dict.get("has_image"))
            img_hash = p_dict.get("image_hash")
            img_drive_id = None

            # 1. Upload image to Google Drive Visual_Vault folder if has_image
            if has_image and img_hash:
                local_img = Path(p_dict.get("image_vault_path") or "")
                if not local_img.exists():
                    local_img = self.lib.vault_media_dir / f"{img_hash}.png"

                if local_img.exists():
                    visual_vault_folder_id = self._get_or_create_folder(service, "Visual_Vault", parent_id=root_id)
                    img_drive_filename = f"{img_hash}.png"

                    # Check if image already exists in Visual_Vault
                    q_img = f"name = '{img_drive_filename}' and '{visual_vault_folder_id}' in parents and trashed = false"
                    res_img = service.files().list(
                        q=q_img, fields="files(id)", supportsAllDrives=True, includeItemsFromAllDrives=True
                    ).execute()
                    files_img = res_img.get("files", [])
                    if files_img:
                        img_drive_id = files_img[0]["id"]
                    else:
                        media_img = MediaFileUpload(str(local_img), mimetype="image/png", resumable=True)
                        body_img = {
                            "name": img_drive_filename,
                            "parents": [visual_vault_folder_id],
                        }
                        created_img = service.files().create(
                            body=body_img,
                            media_body=media_img,
                            fields="id",
                            supportsAllDrives=True,
                        ).execute()
                        img_drive_id = created_img["id"]

            # 2. Upload prompt metadata JSON to Prompts/ folder
            data = {
                "id": p_dict["id"],
                "title": p_dict["title"],
                "platform": p_dict.get("platform", "generic"),
                "prompt_type": p_dict.get("prompt_type", "chat_text"),
                "prompt": p_dict.get("prompt", ""),
                "negative_prompt": p_dict.get("negative_prompt", ""),
                "author": p_dict.get("author", "Member"),
                "tags": p_dict.get("tags", []),
                "variables": p_dict.get("variables", {}),
                "notes": p_dict.get("notes", ""),
                "has_image": 1 if has_image else 0,
                "image_hash": img_hash,
                "image_filename": f"{img_hash}.png" if img_hash else None,
                "image_drive_id": img_drive_id,
                "image_width": p_dict.get("image_width"),
                "image_height": p_dict.get("image_height"),
                "model": p_dict.get("model", ""),
                "sampler": p_dict.get("sampler", ""),
                "cfg_scale": p_dict.get("cfg_scale"),
                "steps": p_dict.get("steps"),
                "seed": p_dict.get("seed"),
                "created_at": p_dict.get("created_at"),
                "updated_at": p_dict.get("updated_at"),
            }
            body = {
                "name": f"prompt_{p_dict['id']}_{p_dict.get('platform', 'generic')}.json",
                "parents": [prompts_folder_id],
            }
            media = MediaInMemoryUpload(
                json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"),
                mimetype="application/json",
                resumable=False,
            )

            gdrive_file_id = p_dict.get("gdrive_file_id")
            if gdrive_file_id:
                try:
                    service.files().update(
                        fileId=gdrive_file_id,
                        media_body=media,
                        supportsAllDrives=True,
                    ).execute()
                except Exception:
                    gdrive_file_id = None

            if not gdrive_file_id:
                created = service.files().create(
                    body=body,
                    media_body=media,
                    fields="id",
                    supportsAllDrives=True,
                ).execute()
                self.lib.update_gdrive_id("prompt", p_dict["id"], created["id"])

        elif item_type == "system_entity":
            entity = self.lib.get_system_entity(item_id)
            if not entity:
                return
            entities_folder_id = self._get_or_create_folder(service, "Gems_and_Skills", parent_id=root_id)
            data = {
                "id": entity.id,
                "title": entity.title,
                "platform": entity.platform,
                "entity_type": entity.entity_type,
                "system_instructions": entity.system_instructions,
                "template": entity.template,
                "variables": entity.variables,
                "author": entity.author,
                "tags": entity.tags,
                "model_hint": entity.model_hint,
                "temperature": entity.temperature,
                "created_at": entity.created_at,
                "updated_at": entity.updated_at,
            }
            body = {
                "name": f"{entity.entity_type}_{entity.id}_{entity.title[:30]}.json",
                "parents": [entities_folder_id],
            }
            media = MediaInMemoryUpload(
                json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"),
                mimetype="application/json",
                resumable=False,
            )

            if entity.gdrive_file_id:
                service.files().update(
                    fileId=entity.gdrive_file_id,
                    media_body=media,
                    supportsAllDrives=True,
                ).execute()
            else:
                created = service.files().create(
                    body=body,
                    media_body=media,
                    fields="id",
                    supportsAllDrives=True,
                ).execute()
                self.lib.update_gdrive_id("system_entity", entity.id, created["id"])

        elif item_type == "company_policy":
            policy_data = self.lib.export_company_policy_dict()
            body = {
                "name": "team_policy.json",
                "parents": [root_id],
            }
            media = MediaInMemoryUpload(
                json.dumps(policy_data, indent=2, ensure_ascii=False).encode("utf-8"),
                mimetype="application/json",
                resumable=False,
            )
            q = f"name = 'team_policy.json' and '{root_id}' in parents and trashed = false"
            res = service.files().list(q=q, fields="files(id)", supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
            files = res.get("files", [])
            if files:
                service.files().update(
                    fileId=files[0]["id"],
                    media_body=media,
                    supportsAllDrives=True,
                ).execute()
            else:
                service.files().create(
                    body=body,
                    media_body=media,
                    fields="id",
                    supportsAllDrives=True,
                ).execute()

    def pull_from_drive(self) -> dict[str, Any]:
        """Pulls team_policy.json, Prompts, Visual_Vault images, and Gems_and_Skills from Google Drive into local database."""
        service = self.build_service()
        cfg = self.load_config()
        root_name = cfg.get("root_folder_name", "PromptVault Team Vault")
        root_id = self._get_or_create_folder(service, root_name)

        synced_policy = False
        synced_prompts = 0
        synced_images = 0
        synced_entities = 0

        # 1. Pull team_policy.json
        try:
            q_pol = f"name = 'team_policy.json' and '{root_id}' in parents and trashed = false"
            pol_res = service.files().list(q=q_pol, fields="files(id)", supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
            pol_files = pol_res.get("files", [])
            if pol_files:
                content_bytes = service.files().get_media(fileId=pol_files[0]["id"], supportsAllDrives=True).execute()
                pol_dict = json.loads(content_bytes.decode("utf-8"))
                self.lib.import_company_policy_dict(pol_dict)
                synced_policy = True
        except Exception:
            pass

        # 2. Locate Visual_Vault folder on Drive (if it exists)
        visual_vault_folder_id = None
        try:
            q_vf = f"name = 'Visual_Vault' and '{root_id}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
            vf_res = service.files().list(q=q_vf, fields="files(id)", supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
            vf_files = vf_res.get("files", [])
            if vf_files:
                visual_vault_folder_id = vf_files[0]["id"]
        except Exception:
            pass

        # 3. Pull Prompts folder
        try:
            q_pf = f"name = 'Prompts' and '{root_id}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
            pf_res = service.files().list(q=q_pf, fields="files(id)", supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
            pf_files = pf_res.get("files", [])
            if pf_files:
                p_folder_id = pf_files[0]["id"]
                p_list = service.files().list(
                    q=f"'{p_folder_id}' in parents and trashed = false",
                    fields="files(id, name)",
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                    pageSize=1000,
                ).execute().get("files", [])
                for f in p_list:
                    if f["name"].endswith(".json"):
                        try:
                            raw = service.files().get_media(fileId=f["id"], supportsAllDrives=True).execute()
                            pdata = json.loads(raw.decode("utf-8"))

                            # Check for associated image in Visual_Vault
                            has_img = bool(pdata.get("has_image"))
                            img_hash = pdata.get("image_hash")
                            if has_img and img_hash:
                                local_img = self.lib.vault_media_dir / f"{img_hash}.png"
                                local_thumb = self.lib.vault_thumbs_dir / f"thumb_{img_hash}.jpg"

                                # If image file doesn't exist locally, download it from Visual_Vault on Drive
                                if not local_img.exists() and visual_vault_folder_id:
                                    img_fn = pdata.get("image_filename") or f"{img_hash}.png"
                                    q_find_img = f"name = '{img_fn}' and '{visual_vault_folder_id}' in parents and trashed = false"
                                    img_search = service.files().list(
                                        q=q_find_img, fields="files(id)", supportsAllDrives=True, includeItemsFromAllDrives=True
                                    ).execute()
                                    found_imgs = img_search.get("files", [])
                                    if found_imgs:
                                        img_bytes = service.files().get_media(fileId=found_imgs[0]["id"], supportsAllDrives=True).execute()
                                        local_img.write_bytes(img_bytes)
                                        synced_images += 1

                                if local_img.exists():
                                    pdata["image_vault_path"] = str(local_img)
                                    if not local_thumb.exists():
                                        try:
                                            from PIL import Image
                                            im = Image.open(local_img)
                                            im.thumbnail((400, 400))
                                            im.convert("RGB").save(local_thumb, "JPEG", quality=85)
                                        except Exception:
                                            pass
                                    if local_thumb.exists():
                                        pdata["thumb_vault_path"] = str(local_thumb)

                            self.lib.upsert_remote_prompt(pdata, gdrive_file_id=f["id"])
                            synced_prompts += 1
                        except Exception as e:
                            self._stats["last_error"] = f"Prompt çekme hatası ({f.get('name')}): {e}"
        except Exception:
            pass

        # 4. Pull Gems_and_Skills folder
        try:
            q_gf = f"name = 'Gems_and_Skills' and '{root_id}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
            gf_res = service.files().list(q=q_gf, fields="files(id)", supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
            gf_files = gf_res.get("files", [])
            if gf_files:
                g_folder_id = gf_files[0]["id"]
                g_list = service.files().list(
                    q=f"'{g_folder_id}' in parents and trashed = false",
                    fields="files(id, name)",
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                    pageSize=1000,
                ).execute().get("files", [])
                for f in g_list:
                    if f["name"].endswith(".json"):
                        try:
                            raw = service.files().get_media(fileId=f["id"], supportsAllDrives=True).execute()
                            edata = json.loads(raw.decode("utf-8"))
                            self.lib.upsert_remote_entity(edata, gdrive_file_id=f["id"])
                            synced_entities += 1
                        except Exception as e:
                            self._stats["last_error"] = f"Gem çekme hatası ({f.get('name')}): {e}"
        except Exception:
            pass

        return {
            "ok": True,
            "synced_policy": synced_policy,
            "synced_prompts": synced_prompts,
            "synced_images": synced_images,
            "synced_entities": synced_entities,
        }
