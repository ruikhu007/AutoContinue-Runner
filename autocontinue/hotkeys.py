"""全局热键（仅 Windows）：Ctrl+Alt+P 暂停全部会话，Ctrl+Alt+R 恢复全部会话。"""
from __future__ import annotations

import sys
import threading
from typing import Callable

MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x1, 0x2, 0x4000
WM_HOTKEY = 0x0312


def start_hotkeys(on_pause: Callable[[], None], on_resume: Callable[[], None], log=print) -> bool:
    if sys.platform != "win32":
        return False

    def loop():
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        mods = MOD_CONTROL | MOD_ALT | MOD_NOREPEAT
        ok_p = user32.RegisterHotKey(None, 1, mods, ord("P"))
        ok_r = user32.RegisterHotKey(None, 2, mods, ord("R"))
        if not (ok_p and ok_r):
            log("[热键] 注册失败（可能被其他程序占用）：Ctrl+Alt+P / Ctrl+Alt+R")
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == WM_HOTKEY:
                try:
                    (on_pause if msg.wParam == 1 else on_resume)()
                except Exception as e:
                    log(f"[热键] 处理失败：{e}")
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    threading.Thread(target=loop, daemon=True, name="acr-hotkeys").start()
    return True
