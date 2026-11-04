"""FastAPI endpoints for Corporate Google Drive management & Real-time Sync."""
from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from ..gdrive_service import CorporateGDriveSync


class DriveConfigRequest(BaseModel):
    shared_drive_id: str = ""
    root_folder_name: str = "PromptVault Team Vault"


class ConnectTokenRequest(BaseModel):
    token: str


def create_gdrive_router(gdrive_sync: CorporateGDriveSync) -> APIRouter:
    router = APIRouter(prefix="/api/team/gdrive", tags=["team-gdrive"])

    @router.get("/status")
    def get_status():
        return gdrive_sync.get_status()

    @router.get("/config")
    def get_config():
        return gdrive_sync.load_config()

    @router.post("/config")
    def update_config(req: DriveConfigRequest):
        gdrive_sync.save_config(
            shared_drive_id=req.shared_drive_id,
            root_folder_name=req.root_folder_name,
        )
        return {"ok": True, "config": gdrive_sync.load_config()}

    @router.post("/upload-service-account")
    async def upload_service_account(file: UploadFile = File(...)):
        content = await file.read()
        success = gdrive_sync.save_service_account_json(content)
        if not success:
            raise HTTPException(400, "Geçersiz Service Account JSON dosyası.")
        return {"ok": True, "status": gdrive_sync.get_status()}

    @router.post("/pull")
    def pull_drive():
        """Pulls prompts, gems, skills, visual vault images, and policy from Google Drive."""
        if not gdrive_sync.is_configured():
            raise HTTPException(400, "Google Drive henüz yapılandırılmamış.")
        try:
            res = gdrive_sync.pull_from_drive()
            return res
        except Exception as e:
            raise HTTPException(500, f"Drive'dan veri çekme başarısız: {e}")

    @router.post("/push")
    def push_drive():
        """Pushes all local prompts, visual vault images, and system entities to Google Drive."""
        if not gdrive_sync.is_configured():
            raise HTTPException(400, "Google Drive henüz yapılandırılmamış.")
        try:
            res = gdrive_sync.push_to_drive()
            return res
        except Exception as e:
            raise HTTPException(500, f"Drive'a gönderme başarısız: {e}")

    @router.get("/connect-token")
    def get_connect_token():
        return {"ok": True, "token": gdrive_sync.generate_connect_token()}

    @router.post("/connect-token")
    def apply_connect_token(req: ConnectTokenRequest):
        try:
            res = gdrive_sync.apply_connect_token(req.token)
            return res
        except Exception as e:
            raise HTTPException(400, str(e))

    return router
