"""Auto-update service for PromptVault Team Edition from GitHub."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("promptvault_team.updater")

REPO_ROOT = Path(__file__).resolve().parent.parent
GITHUB_REPO = "yogruce/promptvault-team"
GITHUB_COMMITS_URL = f"https://api.github.com/repos/{GITHUB_REPO}/commits/main"


class AppUpdater:
    def __init__(self, repo_dir: Path | None = None):
        self.repo_dir = Path(repo_dir) if repo_dir else REPO_ROOT
        self._cache_lock = threading.Lock()
        self._cached_status: dict[str, Any] | None = None
        self._last_checked: float = 0.0

    def is_git_repo(self) -> bool:
        return (self.repo_dir / ".git").is_dir()

    def get_local_commit(self) -> dict[str, str]:
        """Returns short hash, subject message, and date of local HEAD."""
        if self.is_git_repo():
            try:
                res = subprocess.run(
                    ["git", "log", "-1", "--format=%h|%s|%cd", "--date=relative"],
                    cwd=str(self.repo_dir),
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                if res.returncode == 0 and res.stdout.strip():
                    parts = res.stdout.strip().split("|", 2)
                    return {
                        "commit": parts[0] if len(parts) > 0 else "unknown",
                        "message": parts[1] if len(parts) > 1 else "",
                        "date": parts[2] if len(parts) > 2 else "",
                    }
            except Exception as e:
                logger.warning(f"Failed to read local git commit: {e}")
        return {"commit": "v2.0.0", "message": "PromptVault Team", "date": ""}

    def check_for_updates(self, force: bool = False) -> dict[str, Any]:
        """Checks if a newer commit exists on GitHub origin/main."""
        now = time.time()
        with self._cache_lock:
            if not force and self._cached_status and (now - self._last_checked < 60.0):
                return self._cached_status

        local_info = self.get_local_commit()
        local_hash = local_info["commit"]

        remote_hash = ""
        remote_message = ""
        remote_date = ""
        error_msg = None

        # 1. Try git fetch first if inside git repo
        if self.is_git_repo():
            try:
                subprocess.run(
                    ["git", "fetch", "origin", "main"],
                    cwd=str(self.repo_dir),
                    capture_output=True,
                    timeout=8,
                    check=False,
                )
                res_hash = subprocess.run(
                    ["git", "rev-parse", "--short", "origin/main"],
                    cwd=str(self.repo_dir),
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                if res_hash.returncode == 0 and res_hash.stdout.strip():
                    remote_hash = res_hash.stdout.strip()
                    res_log = subprocess.run(
                        ["git", "log", "-1", "--format=%s|%cd", "--date=relative", "origin/main"],
                        cwd=str(self.repo_dir),
                        capture_output=True,
                        text=True,
                        timeout=5,
                        check=False,
                    )
                    if res_log.returncode == 0 and res_log.stdout.strip():
                        parts = res_log.stdout.strip().split("|", 1)
                        remote_message = parts[0]
                        remote_date = parts[1] if len(parts) > 1 else ""
            except Exception as e:
                logger.warning(f"Git fetch check failed: {e}")
                error_msg = str(e)

        # 2. Fallback to GitHub API if remote_hash not obtained via git
        if not remote_hash:
            try:
                req = urllib.request.Request(
                    GITHUB_COMMITS_URL,
                    headers={"User-Agent": "PromptVaultTeam-Updater", "Accept": "application/vnd.github.v3+json"},
                )
                with urllib.request.urlopen(req, timeout=5) as response:
                    if response.status == 200:
                        data = json.loads(response.read().decode("utf-8"))
                        remote_hash = (data.get("sha") or "")[:7]
                        commit_obj = data.get("commit", {})
                        remote_message = commit_obj.get("message", "").split("\n")[0]
                        remote_date = commit_obj.get("author", {}).get("date", "")
            except Exception as e:
                logger.warning(f"GitHub API check failed: {e}")
                if not error_msg:
                    error_msg = f"GitHub bağlantı hatası: {e}"

        has_update = False
        if remote_hash and local_hash and remote_hash != local_hash and local_hash != "unknown":
            has_update = True

        status = {
            "is_git_repo": self.is_git_repo(),
            "local_commit": local_hash,
            "local_message": local_info["message"],
            "local_date": local_info["date"],
            "remote_commit": remote_hash or local_hash,
            "remote_message": remote_message or local_info["message"],
            "remote_date": remote_date or local_info["date"],
            "has_update": has_update,
            "error": error_msg,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

        with self._cache_lock:
            self._cached_status = status
            self._last_checked = now

        return status

    def apply_update(self) -> dict[str, Any]:
        """Pulls latest changes from origin main and schedules server restart (exit code 42)."""
        if not self.is_git_repo():
            raise RuntimeError("Bu dizin bir Git deposu değil. Lütfen güncellemeleri manuel olarak indirin.")

        # 1. Pull latest code from GitHub
        pull_res = subprocess.run(
            ["git", "pull", "origin", "main"],
            cwd=str(self.repo_dir),
            capture_output=True,
            text=True,
            timeout=25,
            check=False,
        )

        if pull_res.returncode != 0:
            err = pull_res.stderr.strip() or pull_res.stdout.strip()
            raise RuntimeError(f"Git pull başarısız oldu: {err}")

        # 2. Update dependencies if needed
        req_file = self.repo_dir / "requirements.txt"
        if req_file.exists():
            try:
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "-r", str(req_file), "--quiet"],
                    cwd=str(self.repo_dir),
                    timeout=30,
                    check=False,
                )
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "-e", ".", "--no-deps", "--quiet"],
                    cwd=str(self.repo_dir),
                    timeout=15,
                    check=False,
                )
            except Exception as e:
                logger.warning(f"Paket güncelleme uyarısı: {e}")

        # 3. Schedule restart via exit code 42 after 1.5 seconds
        def _delayed_restart():
            time.sleep(1.5)
            logger.info("Uygulama güncelleme sonrası yeniden başlatılıyor (exit code 42)...")
            os._exit(42)

        threading.Thread(target=_delayed_restart, daemon=True, name="UpdateRestarter").start()

        return {
            "ok": True,
            "message": "Uygulama başarıyla güncellendi! Birkaç saniye içinde otomatik yeniden başlatılacak.",
            "output": pull_res.stdout.strip(),
            "restarting": True,
        }
