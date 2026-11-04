"""Auto-update service for PromptVault Team Edition from GitHub."""
from __future__ import annotations

import io
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("promptvault_team.updater")

GITHUB_REPO = "yogruce/promptvault-team"
GITHUB_COMMITS_URL = f"https://api.github.com/repos/{GITHUB_REPO}/commits/main"
GITHUB_ZIP_URL = f"https://github.com/{GITHUB_REPO}/archive/refs/heads/main.zip"


def find_repo_root() -> Path:
    """Finds the root repository directory regardless of how the app was launched."""
    cwd = Path.cwd()
    if (cwd / ".git").is_dir() or (cwd / "start_admin.bat").is_file() or (cwd / "setup.py").is_file():
        return cwd

    candidate = Path(__file__).resolve().parent
    for _ in range(5):
        if (candidate / ".git").is_dir() or (candidate / "start_admin.bat").is_file() or (candidate / "setup.py").is_file():
            return candidate
        candidate = candidate.parent

    return Path(__file__).resolve().parent.parent


class AppUpdater:
    def __init__(self, repo_dir: Path | None = None):
        self.repo_dir = Path(repo_dir) if repo_dir else find_repo_root()
        self._cache_lock = threading.Lock()
        self._cached_status: dict[str, Any] | None = None
        self._last_checked: float = 0.0

    def has_git_cli(self) -> bool:
        return shutil.which("git") is not None

    def is_git_repo(self) -> bool:
        return (self.repo_dir / ".git").is_dir() and self.has_git_cli()

    def get_version_file_path() -> Path:
        return self.repo_dir / ".version"

    def get_local_commit(self) -> dict[str, str]:
        """Returns short hash, subject message, and date of local code."""
        # 1. Try git if it's a git repo
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

        # 2. Try .version file
        vfile = self.repo_dir / ".version"
        if vfile.exists():
            try:
                data = json.loads(vfile.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("commit"):
                    return {
                        "commit": str(data["commit"])[:7],
                        "message": str(data.get("message", "PromptVault Team Güncellemesi")),
                        "date": str(data.get("date", "")),
                    }
            except Exception:
                pass

        return {"commit": "v2.0.0", "message": "PromptVault Team", "date": ""}

    def check_for_updates(self, force: bool = False) -> dict[str, Any]:
        """Checks if a newer commit exists on GitHub."""
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

        # 1. Try git fetch first if git is available
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

        # 2. Fallback to GitHub API (works everywhere, even without git!)
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
        if remote_hash and local_hash:
            if local_hash in ("unknown", "v2.0.0") or (remote_hash.lower() != local_hash.lower()):
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
        """Updates the application via git pull or GitHub ZIP archive, then schedules server restart."""
        update_method = "git"
        update_output = ""

        latest_status = self.check_for_updates(force=True)
        remote_commit = latest_status.get("remote_commit") or "latest"
        remote_msg = latest_status.get("remote_message") or ""

        # Method 1: Try Git pull if it's a git repo
        git_success = False
        if self.is_git_repo():
            try:
                pull_res = subprocess.run(
                    ["git", "pull", "origin", "main"],
                    cwd=str(self.repo_dir),
                    capture_output=True,
                    text=True,
                    timeout=25,
                    check=False,
                )
                if pull_res.returncode == 0:
                    git_success = True
                    update_output = pull_res.stdout.strip()
                    update_method = "git"
                else:
                    logger.warning(f"Git pull failed, falling back to ZIP download: {pull_res.stderr}")
            except Exception as e:
                logger.warning(f"Git pull exception, falling back to ZIP download: {e}")

        # Method 2: Fallback to GitHub ZIP download (works 100% reliably on all machines)
        if not git_success:
            update_method = "zip"
            req = urllib.request.Request(GITHUB_ZIP_URL, headers={"User-Agent": "PromptVaultTeam-Updater"})
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    zip_bytes = resp.read()
            except Exception as e:
                raise RuntimeError(f"GitHub'dan güncelleme dosyası indirilemedi: {e}")

            zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
            prefix = ""
            for name in zf.namelist():
                if "/" in name:
                    prefix = name.split("/", 1)[0] + "/"
                    break

            skip_prefixes = (".git", ".venv", "data", "media", "vault_media", "vault_thumbs")
            skip_exact = ("service_account.json", "team_drive_config.json")

            extracted_count = 0
            for member in zf.infolist():
                if member.is_dir():
                    continue
                rel_path = member.filename
                if prefix and rel_path.startswith(prefix):
                    rel_path = rel_path[len(prefix):]
                if not rel_path:
                    continue

                first_part = rel_path.split("/")[0]
                if first_part in skip_prefixes or rel_path in skip_exact or rel_path.endswith(".db"):
                    continue

                target_file = self.repo_dir / rel_path
                target_file.parent.mkdir(parents=True, exist_ok=True)
                content = zf.read(member.filename)
                target_file.write_bytes(content)
                extracted_count += 1

            update_output = f"{extracted_count} dosya güncellendi."

        # Save .version file
        try:
            (self.repo_dir / ".version").write_text(
                json.dumps({
                    "commit": remote_commit,
                    "message": remote_msg,
                    "date": datetime.now(timezone.utc).isoformat(),
                    "method": update_method,
                }, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass

        # Update python packages if pip is available
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

        # Schedule restart via exit code 42
        def _delayed_restart():
            time.sleep(1.5)
            logger.info("Uygulama güncelleme sonrası yeniden başlatılıyor (exit code 42)...")
            os._exit(42)

        threading.Thread(target=_delayed_restart, daemon=True, name="UpdateRestarter").start()

        return {
            "ok": True,
            "message": f"Uygulama başarıyla güncellendi ({update_method.upper()})! Birkaç saniye içinde otomatik yeniden başlatılacak.",
            "output": update_output,
            "restarting": True,
        }
