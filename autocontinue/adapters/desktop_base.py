"""桌面适配器公共部分：专用工作线程（UIA/COM 调用要在固定线程里做）、窗口绑定、用户空闲检测、焦点与剪贴板还原。"""
from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from ..config import Profile
from . import desktop_common as dc
from .base import Adapter

_tls = threading.local()


def _init_com() -> None:
    try:
        import uiautomation as auto
        _tls.uia = auto.UIAutomationInitializerInThread(debug=False)
    except Exception:
        try:
            import ctypes
            ctypes.windll.ole32.CoInitializeEx(None, 2)
        except Exception:
            pass


class DesktopAdapterBase(Adapter):
    def __init__(self, profile: Profile, target: Optional[str]):
        self.profile = profile
        self.target = target
        self.hwnd: int = 0
        self.win: dict = {}
        self._pool = ThreadPoolExecutor(max_workers=1, initializer=_init_com, thread_name_prefix="acr-desk")
        self._own_input_at = 0.0

    async def _run(self, fn, *args):
        return await asyncio.get_running_loop().run_in_executor(self._pool, fn, *args)

    def _bind(self) -> str:
        if self.hwnd and self.attached_desc and dc.is_window(self.hwnd):
            return self.attached_desc
        self.win = dc.find_window(self.profile, self.target)
        self.hwnd = self.win["hwnd"]
        self.target_key = f"desktop:{self.hwnd}"
        self.attached_desc = f"{self.win['title']} [{self.win['process']}]"
        return self.attached_desc

    def _window_problem(self) -> str:
        if not dc.is_window(self.hwnd):
            return "目标窗口已关闭"
        if dc.is_minimized(self.hwnd):
            return "目标窗口被最小化"
        return ""

    async def ready_to_inject(self) -> Optional[str]:
        if not self.steals_focus:
            return None
        idle = dc.user_idle_seconds()
        own = time.monotonic() - self._own_input_at
        if idle < self.profile.timing.idle_user_s and own > idle + 1.0:
            return f"用户 {idle:.0f} 秒前还在操作电脑，推迟注入"
        return None

    def _with_focus(self, action) -> None:
        """切到目标窗口执行 action，结束后还原前台窗口与剪贴板。"""
        prev_fg = dc.get_foreground()
        saved_clip = dc.clipboard_get()
        try:
            if not dc.set_foreground(self.hwnd):
                from .base import InjectError
                raise InjectError("无法把目标窗口切到前台")
            action()
        finally:
            self._own_input_at = time.monotonic()
            time.sleep(0.2)
            try:
                dc.clipboard_set(saved_clip)
            except Exception:
                pass
            if prev_fg and prev_fg != self.hwnd:
                dc.set_foreground(prev_fg)

    async def snapshot(self) -> Optional[bytes]:
        try:
            box = dc.window_rect(self.hwnd)
            return await self._run(lambda: dc.png_bytes(dc.grab(box)))
        except Exception:
            return None

    async def detach(self) -> None:
        self._pool.shutdown(wait=False)
