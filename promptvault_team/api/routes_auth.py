"""Authentication endpoints for PromptVault Team Edition with Google Sign-in support."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from ..db import TeamLibrary


class RegisterRequest(BaseModel):
    email: str
    name: str
    password: str
    role: str = "editor"


class LoginRequest(BaseModel):
    email: str
    password: str


class GoogleLoginRequest(BaseModel):
    email: str = ""
    name: str = ""
    google_id: str = ""
    credential_token: str | None = None


def _decode_google_jwt(token: str) -> dict[str, Any]:
    import base64
    try:
        parts = token.strip().split(".")
        if len(parts) >= 2:
            padded = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
            data = base64.urlsafe_b64decode(padded.encode("utf-8"))
            return json.loads(data.decode("utf-8"))
    except Exception:
        pass
    return {}


class CompanyPolicyRequest(BaseModel):
    company_name: str
    allowed_domains: list[str]
    default_role: str = "editor"
    allow_all_domain_users: bool = True


class MemberPermissionRequest(BaseModel):
    email: str
    role: str = "editor"


def create_auth_router(lib: TeamLibrary) -> APIRouter:
    router = APIRouter(prefix="/api/team/auth", tags=["team-auth"])

    @router.get("/app-mode")
    def get_app_mode():
        return {"mode": lib.mode}

    @router.post("/register")
    def register(req: RegisterRequest):
        try:
            res = lib.register_user(
                email=req.email,
                name=req.name,
                password=req.password,
                role=req.role,
            )
            return {"ok": True, "token": res["token"], "user": res["user"]}
        except (ValueError, PermissionError) as e:
            raise HTTPException(400, str(e))

    @router.post("/login")
    def login(req: LoginRequest):
        try:
            res = lib.login_user(email=req.email, password=req.password)
            return {"ok": True, "token": res["token"], "user": res["user"]}
        except ValueError as e:
            raise HTTPException(401, str(e))

    @router.post("/google")
    def google_auth(req: GoogleLoginRequest):
        """Sign in or register directly using Google OAuth / SSO."""
        email = req.email.strip().lower() if req.email else ""
        name = req.name.strip()
        google_id = req.google_id

        # If credential_token from Google Identity Services is present, decode JWT payload
        if req.credential_token:
            payload = _decode_google_jwt(req.credential_token)
            if payload:
                email = (payload.get("email") or email).strip().lower()
                name = payload.get("name") or name or (email.split("@")[0] if "@" in email else "")
                google_id = payload.get("sub") or google_id

        if not email or "@" not in email:
            raise HTTPException(400, "Geçersiz veya eksik Google hesabı e-posta adresi.")

        try:
            res = lib.login_or_register_google(
                email=email,
                name=name or email.split("@")[0],
                google_id=google_id,
            )
            return {"ok": True, "token": res["token"], "user": res["user"]}
        except PermissionError as e:
            raise HTTPException(403, str(e))

    @router.get("/me")
    def get_me(authorization: str | None = Header(None)):
        if not authorization:
            raise HTTPException(401, "Oturum açılmadı (Token eksik)")
        token = authorization.replace("Bearer ", "").strip()
        user = lib.get_user_by_token(token)
        if not user:
            raise HTTPException(401, "Geçersiz veya süresi dolmuş oturum")
        return {"ok": True, "user": user}

    @router.get("/users")
    def list_users(authorization: str | None = Header(None)):
        token = (authorization or "").replace("Bearer ", "").strip()
        user = lib.get_user_by_token(token)
        if not user or user["role"] != "admin":
            raise HTTPException(403, "Bu işlem için Yönetici (Admin) yetkisi gereklidir.")
        return {"users": lib.list_users()}

    # ------------------------------------------------------------- Company Policy & Whitelist
    @router.get("/company/policy")
    def get_company_policy(authorization: str | None = Header(None)):
        policy = lib.get_company_policy()
        members = lib.list_member_permissions()
        return {
            "ok": True,
            "policy": policy,
            "members": members,
            "mode": lib.mode,
        }

    @router.post("/company/policy")
    def update_company_policy(req: CompanyPolicyRequest, authorization: str | None = Header(None)):
        token = (authorization or "").replace("Bearer ", "").strip()
        user = lib.get_user_by_token(token)
        if not user or user["role"] != "admin":
            raise HTTPException(403, "Bu işlem için Yönetici (Admin) yetkisi gereklidir.")

        updated = lib.update_company_policy(
            company_name=req.company_name,
            allowed_domains=req.allowed_domains,
            default_role=req.default_role,
            allow_all=req.allow_all_domain_users,
        )
        return {"ok": True, "policy": updated}

    @router.post("/company/members")
    def set_company_member(req: MemberPermissionRequest, authorization: str | None = Header(None)):
        token = (authorization or "").replace("Bearer ", "").strip()
        user = lib.get_user_by_token(token)
        if not user or user["role"] != "admin":
            raise HTTPException(403, "Bu işlem için Yönetici (Admin) yetkisi gereklidir.")

        lib.set_member_permission(email=req.email, role=req.role)
        return {"ok": True, "members": lib.list_member_permissions()}

    @router.delete("/company/members/{email}")
    def delete_company_member(email: str, authorization: str | None = Header(None)):
        token = (authorization or "").replace("Bearer ", "").strip()
        user = lib.get_user_by_token(token)
        if not user or user["role"] != "admin":
            raise HTTPException(403, "Bu işlem için Yönetici (Admin) yetkisi gereklidir.")

        lib.delete_member_permission(email=email)
        return {"ok": True, "members": lib.list_member_permissions()}

    return router
