"""Database engine for PromptVault Team Edition.

Features:
- Real User Authentication: Email, salted password hashing, JWT/Session tokens, and enforced roles (admin, editor, viewer)
- Visual Asset Vault: High-resolution images + automatic thumbnail generation
- Auto PNG Metadata Extraction: Reads ComfyUI, A1111, Midjourney, Gemini, ChatGPT metadata from images
- Multi-workspace and role/visibility segmentation
- High-performance SQLite with FTS5 full-text indexing for Prompts and System Entities (Gems/GPTs/Skills)
- Real-time event listeners for background Google Drive sync
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from PIL import Image

from .parsers import parse_metadata
from .pngmeta import read_png_info
from .models import (
    LLMPlatform,
    PromptItem,
    PromptType,
    SystemEntity,
    SystemEntityType,
    Visibility,
    Workspace,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS company_policy (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    company_name TEXT DEFAULT 'Şirket Kasası',
    allowed_domains_json TEXT DEFAULT '[]',
    default_role TEXT DEFAULT 'editor',
    allow_all_domain_users INTEGER DEFAULT 1,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS member_permissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL DEFAULT 'editor',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'editor', -- 'admin' | 'editor' | 'viewer'
    workspace_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT
);

CREATE TABLE IF NOT EXISTS workspaces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    slug TEXT NOT NULL UNIQUE,
    description TEXT,
    icon TEXT DEFAULT '👥',
    color TEXT DEFAULT '#4f46e5',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS system_entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    platform TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    system_instructions TEXT NOT NULL,
    template TEXT DEFAULT '',
    variables_json TEXT DEFAULT '[]',
    description TEXT DEFAULT '',
    tags_json TEXT DEFAULT '[]',
    workspace_id INTEGER REFERENCES workspaces(id) ON DELETE SET NULL,
    author TEXT DEFAULT 'Admin',
    author_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    visibility TEXT DEFAULT 'team',
    model_hint TEXT DEFAULT '',
    temperature REAL DEFAULT 0.7,
    max_tokens INTEGER,
    extra_params_json TEXT DEFAULT '{}',
    gdrive_file_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS prompts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    prompt TEXT NOT NULL,
    negative_prompt TEXT DEFAULT '',
    prompt_type TEXT DEFAULT 'chat_text', -- 'chat_text' | 'image_gen' | 'multimodal'
    platform TEXT DEFAULT 'chatgpt',
    system_entity_id INTEGER REFERENCES system_entities(id) ON DELETE SET NULL,
    variables_json TEXT DEFAULT '{}',
    tags_json TEXT DEFAULT '[]',
    author TEXT DEFAULT 'Member',
    author_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    workspace_id INTEGER REFERENCES workspaces(id) ON DELETE SET NULL,
    visibility TEXT DEFAULT 'team',
    rating INTEGER DEFAULT 0,
    favorite INTEGER DEFAULT 0,
    notes TEXT DEFAULT '',
    source_url TEXT DEFAULT '',
    -- Visual Vault fields
    has_image INTEGER DEFAULT 0,
    image_hash TEXT,
    image_vault_path TEXT,
    thumb_vault_path TEXT,
    image_width INTEGER,
    image_height INTEGER,
    model TEXT DEFAULT '',
    sampler TEXT DEFAULT '',
    cfg_scale REAL,
    steps INTEGER,
    seed INTEGER,
    gdrive_file_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_type TEXT NOT NULL,
    item_id INTEGER,
    action TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    retry_count INTEGER DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- FTS5 full text search tables
CREATE VIRTUAL TABLE IF NOT EXISTS prompt_fts USING fts5(
    title, prompt, negative_prompt, notes, tags,
    content='prompts', content_rowid='id'
);

CREATE VIRTUAL TABLE IF NOT EXISTS entity_fts USING fts5(
    title, system_instructions, description, tags,
    content='system_entities', content_rowid='id'
);

-- Triggers for FTS
CREATE TRIGGER IF NOT EXISTS prompt_ai AFTER INSERT ON prompts BEGIN
    INSERT INTO prompt_fts(rowid, title, prompt, negative_prompt, notes, tags)
    VALUES (new.id, new.title, new.prompt, new.negative_prompt, new.notes, new.tags_json);
END;

CREATE TRIGGER IF NOT EXISTS prompt_ad AFTER DELETE ON prompts BEGIN
    INSERT INTO prompt_fts(prompt_fts, rowid, title, prompt, negative_prompt, notes, tags)
    VALUES ('delete', old.id, old.title, old.prompt, old.negative_prompt, old.notes, old.tags_json);
END;

CREATE TRIGGER IF NOT EXISTS prompt_au AFTER UPDATE ON prompts BEGIN
    INSERT INTO prompt_fts(prompt_fts, rowid, title, prompt, negative_prompt, notes, tags)
    VALUES ('delete', old.id, old.title, old.prompt, old.negative_prompt, old.notes, old.tags_json);
    INSERT INTO prompt_fts(rowid, title, prompt, negative_prompt, notes, tags)
    VALUES (new.id, new.title, new.prompt, new.negative_prompt, new.notes, new.tags_json);
END;

CREATE TRIGGER IF NOT EXISTS entity_ai AFTER INSERT ON system_entities BEGIN
    INSERT INTO entity_fts(rowid, title, system_instructions, description, tags)
    VALUES (new.id, new.title, new.system_instructions, new.description, new.tags_json);
END;

CREATE TRIGGER IF NOT EXISTS entity_ad AFTER DELETE ON system_entities BEGIN
    INSERT INTO entity_fts(entity_fts, rowid, title, system_instructions, description, tags)
    VALUES ('delete', old.id, old.title, old.system_instructions, old.description, old.tags_json);
END;

CREATE TRIGGER IF NOT EXISTS entity_au AFTER UPDATE ON system_entities BEGIN
    INSERT INTO entity_fts(entity_fts, rowid, title, system_instructions, description, tags)
    VALUES ('delete', old.id, old.title, old.system_instructions, old.description, old.tags_json);
    INSERT INTO entity_fts(rowid, title, system_instructions, description, tags)
    VALUES (new.id, new.title, new.system_instructions, new.description, new.tags_json);
END;
"""

TEMPLATE_VAR_REGEX = re.compile(r"\{\{([a-zA-Z0-9_\-]+)\}\}")


def extract_template_variables(text: str) -> list[str]:
    if not text:
        return []
    matches = TEMPLATE_VAR_REGEX.findall(text)
    seen = set()
    result = []
    for m in matches:
        k = m.strip()
        if k and k not in seen:
            seen.add(k)
            result.append(k)
    return result


def _hash_password(password: str, salt: str) -> str:
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()


class TeamLibrary:
    """Thread-safe SQLite database manager for PromptVault Team with User Auth & Visual Vault."""

    def __init__(self, data_dir: str | Path | None = None, mode: str = "admin"):
        if data_dir is None:
            data_dir = Path.home() / ".promptvault_team"
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.mode = mode.lower() if mode in ("admin", "client") else "admin"
        self.db_path = self.data_dir / "team_vault.db"
        self.vault_media_dir = self.data_dir / "media"
        self.vault_media_dir.mkdir(parents=True, exist_ok=True)
        self.vault_thumbs_dir = self.data_dir / "media" / "thumbs"
        self.vault_thumbs_dir.mkdir(parents=True, exist_ok=True)

        self._lock = threading.RLock()
        self._sync_listeners: list[Callable[[str, int, str], None]] = []
        self._init_db()

    def add_sync_listener(self, callback: Callable[[str, int, str], None]) -> None:
        self._sync_listeners.append(callback)

    def _notify_sync(self, item_type: str, item_id: int, action: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        try:
            with self._get_conn() as conn:
                conn.execute(
                    "INSERT INTO sync_queue (item_type, item_id, action, status, created_at, updated_at) "
                    "VALUES (?, ?, ?, 'pending', ?, ?)",
                    (item_type, item_id, action, now, now),
                )
        except Exception:
            pass

        for listener in self._sync_listeners:
            try:
                listener(item_type, item_id, action)
            except Exception:
                pass

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _init_db(self) -> None:
        with self._lock, self._get_conn() as conn:
            conn.executescript(SCHEMA)

            # Auto-migrate prompts table columns if db existed before
            columns = {col[1] for col in conn.execute("PRAGMA table_info(prompts)").fetchall()}
            migrations = [
                ("author_id", "INTEGER"),
                ("has_image", "INTEGER DEFAULT 0"),
                ("image_hash", "TEXT"),
                ("image_vault_path", "TEXT"),
                ("thumb_vault_path", "TEXT"),
                ("image_width", "INTEGER"),
                ("image_height", "INTEGER"),
                ("model", "TEXT DEFAULT ''"),
                ("sampler", "TEXT DEFAULT ''"),
                ("cfg_scale", "REAL"),
                ("steps", "INTEGER"),
                ("seed", "INTEGER"),
                ("gdrive_file_id", "TEXT"),
            ]
            for col_name, col_type in migrations:
                if col_name not in columns:
                    try:
                        conn.execute(f"ALTER TABLE prompts ADD COLUMN {col_name} {col_type}")
                    except Exception:
                        pass

            # Auto-migrate system_entities columns
            ent_cols = {col[1] for col in conn.execute("PRAGMA table_info(system_entities)").fetchall()}
            if "author_id" not in ent_cols:
                try:
                    conn.execute("ALTER TABLE system_entities ADD COLUMN author_id INTEGER")
                except Exception:
                    pass

            # Ensure company_policy exists
            cur = conn.execute("SELECT COUNT(*) FROM company_policy WHERE id = 1")
            if cur.fetchone()[0] == 0:
                now = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    "INSERT INTO company_policy (id, company_name, allowed_domains_json, default_role, allow_all_domain_users, updated_at) "
                    "VALUES (1, 'Şirket Kasası', '[]', 'editor', 1, ?)",
                    (now,),
                )

            # Ensure default workspace
            cur = conn.execute("SELECT COUNT(*) FROM workspaces")
            if cur.fetchone()[0] == 0:
                now = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    "INSERT INTO workspaces (name, slug, description, icon, color, created_at) "
                    "VALUES ('Genel Çalışma Alanı', 'default', 'Tüm ekibin paylaşılan prompt kasası', '🌐', '#4f46e5', ?)",
                    (now,),
                )

    # ------------------------------------------------------------- Company Policy & Domain Matching
    def get_company_policy(self) -> dict[str, Any]:
        with self._get_conn() as conn:
            cur = conn.execute("SELECT * FROM company_policy WHERE id = 1")
            row = cur.fetchone()
            if not row:
                return {"company_name": "Şirket Kasası", "allowed_domains": [], "default_role": "editor", "allow_all_domain_users": True}
            return {
                "company_name": row["company_name"],
                "allowed_domains": json.loads(row["allowed_domains_json"] or "[]"),
                "default_role": row["default_role"] or "editor",
                "allow_all_domain_users": bool(row["allow_all_domain_users"]),
            }

    def update_company_policy(self, company_name: str, allowed_domains: list[str], default_role: str = "editor", allow_all: bool = True) -> dict[str, Any]:
        domains = [d.strip().lower().lstrip("@") for d in allowed_domains if d.strip()]
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._get_conn() as conn:
            conn.execute(
                """
                UPDATE company_policy
                SET company_name = ?, allowed_domains_json = ?, default_role = ?, allow_all_domain_users = ?, updated_at = ?
                WHERE id = 1
                """,
                (company_name.strip(), json.dumps(domains, ensure_ascii=False), default_role, 1 if allow_all else 0, now),
            )
        self._notify_sync("company_policy", 1, "update")
        return self.get_company_policy()

    def set_member_permission(self, email: str, role: str) -> None:
        email = email.strip().lower()
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._get_conn() as conn:
            conn.execute(
                "INSERT INTO member_permissions (email, role, created_at) VALUES (?, ?, ?) "
                "ON CONFLICT(email) DO UPDATE SET role = excluded.role",
                (email, role, now),
            )
            # Also update users table if user already exists
            conn.execute("UPDATE users SET role = ? WHERE email = ?", (role, email))
        self._notify_sync("company_policy", 1, "update")

    def list_member_permissions(self) -> list[dict[str, Any]]:
        with self._get_conn() as conn:
            cur = conn.execute("SELECT email, role, created_at FROM member_permissions ORDER BY id ASC")
            return [dict(row) for row in cur.fetchall()]

    def delete_member_permission(self, email: str) -> None:
        email = email.strip().lower()
        with self._lock, self._get_conn() as conn:
            conn.execute("DELETE FROM member_permissions WHERE email = ?", (email,))
            # Degrade existing user to viewer if deleted from whitelist
            conn.execute("UPDATE users SET role = 'viewer' WHERE email = ?", (email,))
        self._notify_sync("company_policy", 1, "update")

    def export_company_policy_dict(self) -> dict[str, Any]:
        pol = self.get_company_policy()
        members = self.list_member_permissions()
        pol["members"] = members
        pol["updated_at"] = datetime.now(timezone.utc).isoformat()
        return pol

    def import_company_policy_dict(self, data: dict[str, Any]) -> None:
        company_name = data.get("company_name", "Şirket Kasası")
        allowed_domains = data.get("allowed_domains", [])
        default_role = data.get("default_role", "editor")
        allow_all = data.get("allow_all_domain_users", True)
        self.update_company_policy(company_name, allowed_domains, default_role, allow_all)
        for m in data.get("members", []):
            if isinstance(m, dict) and "email" in m and "role" in m:
                self.set_member_permission(m["email"], m["role"])

    def upsert_remote_prompt(self, data: dict[str, Any], gdrive_file_id: str = "") -> int:
        title = data.get("title", "İsimsiz Prompt")
        platform = data.get("platform", "generic")
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._get_conn() as conn:
            cur = conn.execute("SELECT id FROM prompts WHERE title = ? AND platform = ?", (title, platform))
            row = cur.fetchone()
            if row:
                pid = row["id"]
                conn.execute(
                    """
                    UPDATE prompts SET
                        prompt = ?, negative_prompt = ?, author = ?, tags = ?,
                        variables = ?, notes = ?, gdrive_file_id = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        data.get("prompt", ""),
                        data.get("negative_prompt", ""),
                        data.get("author", "Team Member"),
                        json.dumps(data.get("tags", []), ensure_ascii=False) if isinstance(data.get("tags"), list) else str(data.get("tags", "[]")),
                        json.dumps(data.get("variables", []), ensure_ascii=False) if isinstance(data.get("variables"), list) else str(data.get("variables", "[]")),
                        data.get("notes", ""),
                        gdrive_file_id,
                        now,
                        pid,
                    ),
                )
                return pid
            else:
                cur2 = conn.execute(
                    """
                    INSERT INTO prompts (workspace_id, title, platform, prompt_type, prompt, negative_prompt, author, tags, variables, notes, gdrive_file_id, created_at, updated_at)
                    VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        title,
                        platform,
                        data.get("prompt_type", "chat"),
                        data.get("prompt", ""),
                        data.get("negative_prompt", ""),
                        data.get("author", "Team Member"),
                        json.dumps(data.get("tags", []), ensure_ascii=False) if isinstance(data.get("tags"), list) else str(data.get("tags", "[]")),
                        json.dumps(data.get("variables", []), ensure_ascii=False) if isinstance(data.get("variables"), list) else str(data.get("variables", "[]")),
                        data.get("notes", ""),
                        gdrive_file_id,
                        data.get("created_at", now),
                        now,
                    ),
                )
                return cur2.lastrowid

    def upsert_remote_entity(self, data: dict[str, Any], gdrive_file_id: str = "") -> int:
        title = data.get("title", "İsimsiz Gem")
        platform = data.get("platform", "gemini")
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._get_conn() as conn:
            cur = conn.execute("SELECT id FROM system_entities WHERE title = ? AND platform = ?", (title, platform))
            row = cur.fetchone()
            if row:
                eid = row["id"]
                conn.execute(
                    """
                    UPDATE system_entities SET
                        system_instructions = ?, template = ?, variables = ?, author = ?,
                        tags = ?, model_hint = ?, temperature = ?, gdrive_file_id = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        data.get("system_instructions", ""),
                        data.get("template", ""),
                        json.dumps(data.get("variables", []), ensure_ascii=False) if isinstance(data.get("variables"), list) else str(data.get("variables", "[]")),
                        data.get("author", "Team Member"),
                        json.dumps(data.get("tags", []), ensure_ascii=False) if isinstance(data.get("tags"), list) else str(data.get("tags", "[]")),
                        data.get("model_hint", ""),
                        data.get("temperature", 0.7),
                        gdrive_file_id,
                        now,
                        eid,
                    ),
                )
                return eid
            else:
                cur2 = conn.execute(
                    """
                    INSERT INTO system_entities (workspace_id, platform, entity_type, title, system_instructions, template, variables, author, tags, model_hint, temperature, gdrive_file_id, created_at, updated_at)
                    VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        platform,
                        data.get("entity_type", "gem"),
                        title,
                        data.get("system_instructions", ""),
                        data.get("template", ""),
                        json.dumps(data.get("variables", []), ensure_ascii=False) if isinstance(data.get("variables"), list) else str(data.get("variables", "[]")),
                        data.get("author", "Team Member"),
                        json.dumps(data.get("tags", []), ensure_ascii=False) if isinstance(data.get("tags"), list) else str(data.get("tags", "[]")),
                        data.get("model_hint", ""),
                        data.get("temperature", 0.7),
                        gdrive_file_id,
                        data.get("created_at", now),
                        now,
                    ),
                )
                return cur2.lastrowid

    # ------------------------------------------------------------- Authentication & Users
    def login_or_register_google(self, email: str, name: str, google_id: str = "") -> dict[str, Any]:
        """Sign in or register automatically using Google Workspace Domain Matching."""
        email = email.strip().lower()
        name = name.strip() or email.split("@")[0]
        domain = email.split("@")[-1] if "@" in email else ""
        now = datetime.now(timezone.utc).isoformat()
        session_token = secrets.token_urlsafe(32)

        with self._lock, self._get_conn() as conn:
            # 1. Is this user already registered?
            cur = conn.execute("SELECT * FROM users WHERE email = ?", (email,))
            row = cur.fetchone()
            if row:
                user_id = row["id"]
                user_name = row["name"]
                if name and name != user_name:
                    conn.execute("UPDATE users SET name = ?, updated_at = ? WHERE id = ?", (name, now, user_id))
                    user_name = name
                user_data = {
                    "id": user_id,
                    "email": row["email"],
                    "name": user_name,
                    "role": row["role"],
                    "workspace_id": row["workspace_id"],
                }
                conn.execute(
                    "INSERT INTO user_sessions (token, user_id, created_at) VALUES (?, ?, ?)",
                    (session_token, user_id, now),
                )
                return {"token": session_token, "user": user_data}

            # 2. Check permissions based on Mode
            c2 = conn.execute("SELECT COUNT(*) FROM users")
            user_count = c2.fetchone()[0]

            # Read company policy directly
            pol_row = conn.execute("SELECT * FROM company_policy WHERE id = 1").fetchone()
            allowed_domains = json.loads(pol_row["allowed_domains_json"] or "[]") if pol_row else []
            default_role = pol_row["default_role"] if pol_row and pol_row["default_role"] else "editor"
            allow_all_domain_users = bool(pol_row["allow_all_domain_users"]) if pol_row else True

            p_cur = conn.execute("SELECT role FROM member_permissions WHERE email = ?", (email,))
            p_row = p_cur.fetchone()

            if self.mode == "client":
                # CLIENT MODE: Never grant admin automatically
                if p_row:
                    role = p_row["role"]
                elif allowed_domains and domain in allowed_domains and allow_all_domain_users:
                    role = default_role
                elif not allowed_domains:
                    role = default_role or "editor"
                else:
                    domains_str = ", ".join([f"@{d}" for d in allowed_domains]) if allowed_domains else "tanımlanmış şirket domaini"
                    raise PermissionError(
                        f"Erişim Reddedildi: '{email}' hesabı bu şirket kasasında yetkilendirilmemiştir ({domains_str}). "
                        f"Lütfen şirket yöneticinizden e-posta adresinizi onaylatın."
                    )
            else:
                # ADMIN MODE
                if user_count == 0:
                    role = "admin"
                    if domain and domain not in allowed_domains:
                        allowed_domains.append(domain)
                        conn.execute(
                            "UPDATE company_policy SET allowed_domains_json = ?, updated_at = ? WHERE id = 1",
                            (json.dumps(allowed_domains, ensure_ascii=False), now),
                        )
                else:
                    if p_row:
                        role = p_row["role"]
                    elif allowed_domains and domain in allowed_domains and allow_all_domain_users:
                        role = default_role
                    elif not allowed_domains:
                        role = default_role
                    else:
                        domains_str = ", ".join([f"@{d}" for d in allowed_domains])
                        raise PermissionError(
                            f"Erişim Reddedildi: Bu kasa yalnızca şirket çalışanlarına açıktır ({domains_str}). "
                            f"'{email}' hesabı onaylı listede bulunamadı."
                        )

            # Register new authorized user
            salt = secrets.token_hex(16)
            pw_hash = _hash_password(secrets.token_urlsafe(16), salt)

            cur = conn.execute(
                """
                INSERT INTO users (email, name, password_hash, salt, role, workspace_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (email, name, pw_hash, salt, role, now, now),
            )
            user_id = cur.lastrowid
            user_data = {
                "id": user_id,
                "email": email,
                "name": name,
                "role": role,
                "workspace_id": 1,
            }
            conn.execute(
                "INSERT INTO user_sessions (token, user_id, created_at) VALUES (?, ?, ?)",
                (session_token, user_id, now),
            )

        return {"token": session_token, "user": user_data}

    def register_user(self, email: str, name: str, password: str, role: str = "editor", workspace_id: int | None = 1) -> dict[str, Any]:
        email = email.strip().lower()
        name = name.strip()
        domain = email.split("@")[-1] if "@" in email else ""
        if not email or "@" not in email:
            raise ValueError("Geçerli bir e-posta adresi giriniz.")
        if len(password) < 4:
            raise ValueError("Şifre en az 4 karakter olmalıdır.")

        now = datetime.now(timezone.utc).isoformat()
        session_token = secrets.token_urlsafe(32)

        with self._lock, self._get_conn() as conn:
            cur = conn.execute("SELECT COUNT(*) FROM users")
            user_count = cur.fetchone()[0]

            # Read company policy directly
            pol_row = conn.execute("SELECT * FROM company_policy WHERE id = 1").fetchone()
            allowed_domains = json.loads(pol_row["allowed_domains_json"] or "[]") if pol_row else []
            default_role = pol_row["default_role"] if pol_row and pol_row["default_role"] else "editor"
            allow_all_domain_users = bool(pol_row["allow_all_domain_users"]) if pol_row else True

            p_cur = conn.execute("SELECT role FROM member_permissions WHERE email = ?", (email,))
            p_row = p_cur.fetchone()

            if self.mode == "client":
                # CLIENT MODE: Never grant admin automatically
                if p_row:
                    role = p_row["role"]
                elif allowed_domains and domain in allowed_domains and allow_all_domain_users:
                    role = default_role
                elif not allowed_domains:
                    role = default_role or "editor"
                else:
                    domains_str = ", ".join([f"@{d}" for d in allowed_domains]) if allowed_domains else "tanımlanmış şirket domaini"
                    raise PermissionError(
                        f"Erişim Reddedildi: '{email}' hesabı bu şirket kasasında yetkilendirilmemiştir ({domains_str}). "
                        f"Lütfen şirket yöneticinizden e-posta adresinizi onaylatın."
                    )
            else:
                # ADMIN MODE
                if user_count == 0:
                    role = "admin"  # First user is root Administrator in admin mode
                    if domain and domain not in allowed_domains:
                        allowed_domains.append(domain)
                        conn.execute(
                            "UPDATE company_policy SET allowed_domains_json = ?, updated_at = ? WHERE id = 1",
                            (json.dumps(allowed_domains, ensure_ascii=False), now),
                        )
                else:
                    if p_row:
                        role = p_row["role"]
                    elif allowed_domains and domain in allowed_domains and allow_all_domain_users:
                        role = default_role
                    elif not allowed_domains:
                        role = default_role or "editor"
                    else:
                        domains_str = ", ".join([f"@{d}" for d in allowed_domains])
                        raise PermissionError(
                            f"Erişim Reddedildi: Bu kasa yalnızca şirket çalışanlarına açıktır ({domains_str}). "
                            f"'{email}' hesabı onaylı listede bulunamadı."
                        )

            salt = secrets.token_hex(16)
            pw_hash = _hash_password(password, salt)

            try:
                cur = conn.execute(
                    """
                    INSERT INTO users (email, name, password_hash, salt, role, workspace_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (email, name, pw_hash, salt, role, workspace_id, now, now),
                )
                user_id = cur.lastrowid
            except sqlite3.IntegrityError:
                raise ValueError("Bu e-posta adresi ile zaten bir hesap kayıtlı.")

            conn.execute(
                "INSERT INTO user_sessions (token, user_id, created_at) VALUES (?, ?, ?)",
                (session_token, user_id, now),
            )

        return {
            "token": session_token,
            "user": {
                "id": user_id,
                "email": email,
                "name": name,
                "role": role,
                "workspace_id": workspace_id,
            },
        }

    def login_user(self, email: str, password: str) -> dict[str, Any]:
        email = email.strip().lower()
        with self._get_conn() as conn:
            cur = conn.execute("SELECT * FROM users WHERE email = ?", (email,))
            row = cur.fetchone()
            if not row:
                raise ValueError("E-posta adresi veya şifre hatalı.")

            pw_hash = _hash_password(password, row["salt"])
            if pw_hash != row["password_hash"]:
                raise ValueError("E-posta adresi veya şifre hatalı.")

            user_id = row["id"]
            user_data = {
                "id": user_id,
                "email": row["email"],
                "name": row["name"],
                "role": row["role"],
                "workspace_id": row["workspace_id"],
            }

        token = self.create_session(user_id)
        return {"token": token, "user": user_data}

    def create_session(self, user_id: int) -> str:
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._get_conn() as conn:
            conn.execute(
                "INSERT INTO user_sessions (token, user_id, created_at) VALUES (?, ?, ?)",
                (token, user_id, now),
            )
        return token

    def get_user_by_token(self, token: str) -> dict[str, Any] | None:
        if not token:
            return None
        with self._get_conn() as conn:
            cur = conn.execute(
                """
                SELECT u.id, u.email, u.name, u.role, u.workspace_id, u.created_at
                FROM user_sessions s
                JOIN users u ON u.id = s.user_id
                WHERE s.token = ?
                """,
                (token,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "id": row["id"],
                "email": row["email"],
                "name": row["name"],
                "role": row["role"],
                "workspace_id": row["workspace_id"],
                "created_at": row["created_at"],
            }

    def list_users(self) -> list[dict[str, Any]]:
        with self._get_conn() as conn:
            cur = conn.execute("SELECT id, email, name, role, workspace_id, created_at FROM users ORDER BY id ASC")
            return [dict(row) for row in cur.fetchall()]

    # ------------------------------------------------------------- Workspaces
    def list_workspaces(self) -> list[Workspace]:
        with self._get_conn() as conn:
            cur = conn.execute("SELECT * FROM workspaces ORDER BY id ASC")
            return [
                Workspace(
                    id=row["id"],
                    name=row["name"],
                    slug=row["slug"],
                    description=row["description"] or "",
                    icon=row["icon"] or "👥",
                    color=row["color"] or "#4f46e5",
                    created_at=row["created_at"],
                )
                for row in cur.fetchall()
            ]

    def create_workspace(self, name: str, slug: str | None = None, description: str = "", icon: str = "📁", color: str = "#6366f1") -> Workspace:
        if not slug:
            slug = re.sub(r"[^a-zA-Z0-9_-]", "-", name.lower()).strip("-")
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._get_conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO workspaces (name, slug, description, icon, color, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (name, slug, description, icon, color, now),
            )
            wid = cur.lastrowid
            return Workspace(
                id=wid,
                name=name,
                slug=slug,
                description=description,
                icon=icon,
                color=color,
                created_at=now,
            )

    # --------------------------------------------------------- System Entities (Gems, GPTs, Skills)
    def create_system_entity(
        self,
        title: str,
        platform: str,
        entity_type: str,
        system_instructions: str,
        template: str = "",
        description: str = "",
        tags: list[str] | None = None,
        workspace_id: int | None = None,
        author: str = "Admin",
        author_id: int | None = None,
        visibility: str = Visibility.TEAM.value,
        model_hint: str = "",
        temperature: float = 0.7,
        max_tokens: int | None = None,
        extra_params: dict[str, Any] | None = None,
    ) -> SystemEntity:
        now = datetime.now(timezone.utc).isoformat()
        variables = extract_template_variables(template or system_instructions)
        tags = tags or []
        extra_params = extra_params or {}

        with self._lock, self._get_conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO system_entities (
                    title, platform, entity_type, system_instructions, template,
                    variables_json, description, tags_json, workspace_id, author,
                    author_id, visibility, model_hint, temperature, max_tokens, extra_params_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    title.strip(),
                    platform.strip().lower(),
                    entity_type.strip().lower(),
                    system_instructions.strip(),
                    template.strip(),
                    json.dumps(variables, ensure_ascii=False),
                    description.strip(),
                    json.dumps(tags, ensure_ascii=False),
                    workspace_id,
                    author.strip(),
                    author_id,
                    visibility,
                    model_hint.strip(),
                    temperature,
                    max_tokens,
                    json.dumps(extra_params, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            entity_id = cur.lastrowid

        self._notify_sync("system_entity", entity_id, "upsert")
        return self.get_system_entity(entity_id)  # type: ignore

    def get_system_entity(self, entity_id: int) -> SystemEntity | None:
        with self._get_conn() as conn:
            cur = conn.execute("SELECT * FROM system_entities WHERE id = ?", (entity_id,))
            row = cur.fetchone()
            if not row:
                return None
            return SystemEntity(
                id=row["id"],
                title=row["title"],
                platform=row["platform"],
                entity_type=row["entity_type"],
                system_instructions=row["system_instructions"],
                template=row["template"] or "",
                variables=json.loads(row["variables_json"] or "[]"),
                description=row["description"] or "",
                tags=json.loads(row["tags_json"] or "[]"),
                workspace_id=row["workspace_id"],
                author=row["author"] or "Admin",
                visibility=row["visibility"] or "team",
                model_hint=row["model_hint"] or "",
                temperature=row["temperature"] if row["temperature"] is not None else 0.7,
                max_tokens=row["max_tokens"],
                extra_params=json.loads(row["extra_params_json"] or "{}"),
                gdrive_file_id=row["gdrive_file_id"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )

    def list_system_entities(
        self,
        platform: str | None = None,
        entity_type: str | None = None,
        workspace_id: int | None = None,
        search_query: str | None = None,
        limit: int = 100,
    ) -> list[SystemEntity]:
        with self._get_conn() as conn:
            if search_query:
                q = """
                SELECT se.* FROM system_entities se
                JOIN entity_fts f ON f.rowid = se.id
                WHERE entity_fts MATCH ?
                """
                params: list[Any] = [search_query]
            else:
                q = "SELECT se.* FROM system_entities se WHERE 1=1"
                params = []

            if platform:
                q += " AND se.platform = ?"
                params.append(platform.lower())
            if entity_type:
                q += " AND se.entity_type = ?"
                params.append(entity_type.lower())
            if workspace_id is not None:
                q += " AND (se.workspace_id = ? OR se.visibility = 'company')"
                params.append(workspace_id)

            q += " ORDER BY se.id DESC LIMIT ?"
            params.append(limit)

            cur = conn.execute(q, params)
            results = []
            for row in cur.fetchall():
                results.append(
                    SystemEntity(
                        id=row["id"],
                        title=row["title"],
                        platform=row["platform"],
                        entity_type=row["entity_type"],
                        system_instructions=row["system_instructions"],
                        template=row["template"] or "",
                        variables=json.loads(row["variables_json"] or "[]"),
                        description=row["description"] or "",
                        tags=json.loads(row["tags_json"] or "[]"),
                        workspace_id=row["workspace_id"],
                        author=row["author"] or "Admin",
                        visibility=row["visibility"] or "team",
                        model_hint=row["model_hint"] or "",
                        temperature=row["temperature"] if row["temperature"] is not None else 0.7,
                        max_tokens=row["max_tokens"],
                        extra_params=json.loads(row["extra_params_json"] or "{}"),
                        gdrive_file_id=row["gdrive_file_id"],
                        created_at=row["created_at"],
                        updated_at=row["updated_at"],
                    )
                )
            return results

    # ------------------------------------------------------------- Visual Vault & Prompts
    def add_prompt_with_asset(
        self,
        title: str,
        prompt: str,
        negative_prompt: str = "",
        prompt_type: str = PromptType.CHAT_TEXT.value,
        platform: str = LLMPlatform.CHATGPT.value,
        system_entity_id: int | None = None,
        tags: list[str] | None = None,
        author: str = "Member",
        author_id: int | None = None,
        workspace_id: int | None = None,
        visibility: str = Visibility.TEAM.value,
        image_bytes: bytes | None = None,
        notes: str = "",
        source_url: str = "",
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        tags = tags or []

        has_image = 0
        image_hash = None
        image_vault_path = None
        thumb_vault_path = None
        w, h = None, None
        model = ""
        sampler = ""
        cfg_scale = None
        steps = None
        seed = None

        # Auto-parse image metadata if provided
        if image_bytes:
            has_image = 1
            image_hash = hashlib.sha256(image_bytes).hexdigest()
            fn = f"{image_hash}.png"
            dest = self.vault_media_dir / fn
            dest.write_bytes(image_bytes)
            image_vault_path = str(dest)

            # Generate Thumbnail
            try:
                img = Image.open(io.BytesIO(image_bytes))
                w, h = img.size
                img.thumbnail((400, 400))
                thumb_dest = self.vault_thumbs_dir / f"thumb_{image_hash}.jpg"
                img.convert("RGB").save(thumb_dest, "JPEG", quality=85)
                thumb_vault_path = str(thumb_dest)
            except Exception:
                pass

            # Try reading PNG metadata (A1111, ComfyUI, Midjourney etc.)
            try:
                info = read_png_info(dest)
                parsed = parse_metadata(info.text)
                if parsed:
                    if not prompt and parsed.prompt:
                        prompt = parsed.prompt
                    if not negative_prompt and parsed.negative:
                        negative_prompt = parsed.negative
                    model = parsed.model or ""
                    sampler = parsed.sampler or ""
                    cfg_scale = parsed.cfg_scale
                    steps = parsed.steps
                    seed = parsed.seed
                    if parsed.generator:
                        platform = parsed.generator.lower()
                    prompt_type = PromptType.IMAGE_GEN.value
            except Exception:
                pass

        title = title.strip() or (prompt[:40] + "..." if len(prompt) > 40 else prompt) or "İsimsiz Prompt"

        with self._lock, self._get_conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO prompts (
                    title, prompt, negative_prompt, prompt_type, platform,
                    system_entity_id, tags_json, author, author_id, workspace_id,
                    visibility, has_image, image_hash, image_vault_path, thumb_vault_path,
                    image_width, image_height, model, sampler, cfg_scale, steps, seed,
                    notes, source_url, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    title,
                    prompt.strip(),
                    negative_prompt.strip(),
                    prompt_type,
                    platform.lower(),
                    system_entity_id,
                    json.dumps(tags, ensure_ascii=False),
                    author.strip(),
                    author_id,
                    workspace_id,
                    visibility,
                    has_image,
                    image_hash,
                    image_vault_path,
                    thumb_vault_path,
                    w,
                    h,
                    model,
                    sampler,
                    cfg_scale,
                    steps,
                    seed,
                    notes.strip(),
                    source_url.strip(),
                    now,
                    now,
                ),
            )
            pid = cur.lastrowid

        self._notify_sync("prompt", pid, "upsert")
        return self.get_prompt_dict(pid)  # type: ignore

    def get_prompt_dict(self, prompt_id: int) -> dict[str, Any] | None:
        with self._get_conn() as conn:
            cur = conn.execute("SELECT * FROM prompts WHERE id = ?", (prompt_id,))
            row = cur.fetchone()
            if not row:
                return None
            res = dict(row)
            res["tags"] = json.loads(res.get("tags_json") or "[]")
            res["variables"] = json.loads(res.get("variables_json") or "{}")
            return res

    def get_prompt(self, prompt_id: int) -> PromptItem | None:
        d = self.get_prompt_dict(prompt_id)
        if not d:
            return None
        return PromptItem(
            id=d["id"],
            workspace_id=d.get("workspace_id"),
            platform=d.get("platform", "chatgpt"),
            prompt_type=d.get("prompt_type", "chat"),
            title=d.get("title", ""),
            prompt=d.get("prompt", ""),
            negative_prompt=d.get("negative_prompt", ""),
            system_entity_id=d.get("system_entity_id"),
            tags=d.get("tags", []),
            variables=d.get("variables", {}),
            author=d.get("author", "Member"),
            notes=d.get("notes", ""),
            gdrive_file_id=d.get("gdrive_file_id"),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
        )

    def add_prompt(self, **kwargs) -> PromptItem:
        d = self.add_prompt_with_asset(**kwargs)
        return self.get_prompt(d["id"])  # type: ignore

    def list_prompts(self, **kwargs) -> list[PromptItem]:
        dicts = self.list_prompts_dict(**kwargs)
        items = []
        for d in dicts:
            p = self.get_prompt(d["id"])
            if p:
                items.append(p)
        return items

    def list_prompts_dict(
        self,
        platform: str | None = None,
        prompt_type: str | None = None,
        search_query: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self._get_conn() as conn:
            if search_query:
                q = """
                SELECT p.* FROM prompts p
                JOIN prompt_fts f ON f.rowid = p.id
                WHERE prompt_fts MATCH ?
                """
                params: list[Any] = [search_query]
            else:
                q = "SELECT p.* FROM prompts p WHERE 1=1"
                params = []

            if platform and platform != "all":
                q += " AND p.platform = ?"
                params.append(platform.lower())
            if prompt_type and prompt_type != "all":
                q += " AND p.prompt_type = ?"
                params.append(prompt_type.lower())

            q += " ORDER BY p.id DESC LIMIT ?"
            params.append(limit)

            cur = conn.execute(q, params)
            results = []
            for row in cur.fetchall():
                d = dict(row)
                d["tags"] = json.loads(d.get("tags_json") or "[]")
                d["variables"] = json.loads(d.get("variables_json") or "{}")
                results.append(d)
            return results
