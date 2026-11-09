"""以远程调试端口启动 Chrome / Edge（专用配置目录，首次需在其中登录各家大模型）。"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from .config import RunnerConfig


def find_browser(preferred: Optional[str] = None) -> Optional[str]:
    if preferred and Path(preferred).exists():
        return preferred
    if sys.platform == "win32":
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        local = os.environ.get("LOCALAPPDATA", "")
        for p in (rf"{pf}\Google\Chrome\Application\chrome.exe", rf"{pf86}\Google\Chrome\Application\chrome.exe",
                  rf"{local}\Google\Chrome\Application\chrome.exe",
                  rf"{pf86}\Microsoft\Edge\Application\msedge.exe", rf"{pf}\Microsoft\Edge\Application\msedge.exe"):
            if p and Path(p).exists():
                return p
        return None
    if sys.platform == "darwin":
        p = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        return p if Path(p).exists() else None
    return shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")


def launch_chrome(cfg: RunnerConfig, urls: list[str] | None = None) -> str:
    exe = find_browser(cfg.chrome_path)
    if not exe:
        raise RuntimeError("没有找到 Chrome 或 Edge，请在 runner.yaml 的 chrome_path 中填写浏览器路径")
    port = urlparse(cfg.cdp_endpoint).port or 9230
    data_dir = cfg.resolve(cfg.chrome_user_data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    args = [exe, f"--remote-debugging-port={port}", f"--user-data-dir={data_dir}", "--no-first-run",
            "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
            "--disable-backgrounding-occluded-windows", *(urls or [])]
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(args, creationflags=flags, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    return f"已启动 {Path(exe).name}，调试端口 {port}，配置目录 {data_dir}"
