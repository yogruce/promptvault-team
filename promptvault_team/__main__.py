"""Main entry point for running PromptVault Team Edition directly:
python -m promptvault_team [--role admin|client] [--port 7861] [--open]
"""
from __future__ import annotations

import argparse
import sys
import time
import webbrowser
from pathlib import Path


def _free_port_windows(port: int) -> None:
    if sys.platform != "win32":
        return
    import os
    import subprocess
    current_pid = os.getpid()
    try:
        cmd = f'powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue | ForEach-Object {{ if ($_.OwningProcess -ne {current_pid}) {{ Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }} }}"'
        subprocess.run(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="promptvault_team",
        description="PromptVault Team Edition - ChatGPT, Gemini, Claude, Gems & Google Workspace Drive Sync",
    )
    parser.add_argument("--role", choices=["admin", "client"], default="admin", help="Çalışma modu: admin veya client")
    parser.add_argument("--port", type=int, default=None, help="Port numarası (Varsayılan: admin için 7861, client için 7862)")
    parser.add_argument("--host", default="127.0.0.1", help="Sunucu host adresi")
    parser.add_argument("--open", action="store_true", help="Tarayıcıda otomatik aç")
    parser.add_argument("--data-dir", default=None, help="Özel veritabanı dizini")
    parser.add_argument("--auto-close", dest="auto_close", action="store_true", default=True, help="Tarayıcı kapandığında terminali otomatik kapat")
    parser.add_argument("--no-auto-close", dest="auto_close", action="store_false", help="Tarayıcı kapansa bile sunucuyu açık tut")

    args = parser.parse_args()

    port = args.port
    if port is None:
        port = 7861 if args.role == "admin" else 7862

    _free_port_windows(port)

    import uvicorn
    from promptvault_team.app import create_team_app

    url = f"http://{'127.0.0.1' if args.host == '0.0.0.0' else args.host}:{port}"
    print(f"\n========================================================")
    print(f"🚀 PromptVault Team Sunucusu ({args.role.upper()} Modu): {url}")
    print(f"◈ ChatGPT, Gemini, Claude, Gems/Skills & Kurumsal Drive")
    print(f"========================================================\n")

    if args.open:
        import threading

        def _delayed_open():
            time.sleep(0.6)
            webbrowser.open(f"{url}/?t={int(time.time())}")

        threading.Thread(target=_delayed_open, daemon=True).start()

    app = create_team_app(data_dir=args.data_dir, mode=args.role, auto_close=args.auto_close)
    try:
        uvicorn.run(app, host=args.host, port=port, log_level="warning")
    except OSError as e:
        if "10048" in str(e) or getattr(e, "winerror", None) == 10048:
            _free_port_windows(port)
            time.sleep(0.5)
            uvicorn.run(app, host=args.host, port=port, log_level="warning")
        else:
            raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
