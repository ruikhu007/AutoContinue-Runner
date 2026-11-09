"""通知：控制台输出、提示音，以及可选的 webhook（接天枢 / 动作打印机消息通道）。"""
from __future__ import annotations

import json
import sys
import threading
import urllib.request
from datetime import datetime
from typing import Any, Callable, Optional

from .config import NotifyCfg

Listener = Callable[[dict[str, Any]], None]


class Notifier:
    def __init__(self, cfg: NotifyCfg):
        self.cfg = cfg
        self.listeners: list[Listener] = []

    def subscribe(self, fn: Listener) -> None:
        self.listeners.append(fn)

    def send(self, level: str, session_id: str, name: str, message: str, **extra: Any) -> None:
        payload = {"time": datetime.now().isoformat(timespec="seconds"), "level": level,
                   "session": session_id, "name": name, "message": message, **extra}
        print(f"[{payload['time']}] [{level}] {name}: {message}", flush=True)
        if self.cfg.beep and level in ("pause", "done", "error"):
            _beep(level)
        for fn in list(self.listeners):
            try:
                fn(payload)
            except Exception as e:  # 监听者异常不能影响主流程
                print(f"[通知] 监听者异常：{e}", file=sys.stderr)
        if self.cfg.webhook and level in ("pause", "done", "error"):
            threading.Thread(target=_post, args=(self.cfg.webhook, payload), daemon=True).start()


def _beep(level: str) -> None:
    if sys.platform != "win32":
        return
    try:
        import winsound
        winsound.MessageBeep(winsound.MB_ICONASTERISK if level == "done" else winsound.MB_ICONEXCLAMATION)
    except Exception:
        pass


def _post(url: str, payload: dict[str, Any], timeout: float = 8.0) -> Optional[int]:
    try:
        req = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except Exception as e:
        print(f"[通知] webhook 发送失败：{e}", file=sys.stderr)
        return None
