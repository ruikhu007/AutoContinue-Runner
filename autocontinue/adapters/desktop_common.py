"""Windows 桌面通用工具：窗口枚举与匹配、前台切换、用户空闲检测、截图、感知哈希。仅在 Windows 上导入。"""
from __future__ import annotations

import ctypes
import hashlib
import io
import os
import sys
import time
from ctypes import wintypes
from typing import Optional

from ..config import Profile

if sys.platform != "win32":  # pragma: no cover
    raise ImportError("desktop_common 只能在 Windows 上使用")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# 让坐标与截图使用物理像素（高 DPI 屏幕下与 mss 一致）
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass

user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                 ctypes.POINTER(wintypes.DWORD)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

_EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


# ---------------------------------------------------------------- 窗口

def process_name(pid: int) -> str:
    h = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower()
        return ""
    finally:
        kernel32.CloseHandle(h)


def window_info(hwnd: int) -> dict:
    n = user32.GetWindowTextLengthW(hwnd)
    title = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, title, n + 1)
    cls = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, cls, 256)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return {"hwnd": int(hwnd), "title": title.value, "class_name": cls.value, "pid": pid.value,
            "process": process_name(pid.value)}


def list_windows() -> list[dict]:
    out: list[dict] = []

    def cb(hwnd, _lparam):
        if hwnd and user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
            out.append(window_info(hwnd))
        return True

    user32.EnumWindows(_EnumProc(cb), 0)
    return out


def window_matches(profile: Profile, w: dict) -> bool:
    m = profile.match
    if not (m.process or m.title_contains or m.class_name):
        return False
    if m.process and w["process"] != m.process.lower():
        return False
    if m.title_contains and m.title_contains not in w["title"]:
        return False
    if m.class_name and w["class_name"] != m.class_name:
        return False
    return True


def find_window(profile: Profile, target: Optional[str]) -> dict:
    wins = list_windows()
    if target:
        for w in wins:
            if str(w["hwnd"]) == str(target):
                return w
        raise RuntimeError(f"找不到窗口句柄 {target}，窗口可能已关闭")
    for w in wins:
        if window_matches(profile, w):
            return w
    raise RuntimeError(f"没有找到匹配画像“{profile.name or profile.id}”的窗口，请先打开该应用")


def list_desktop_targets(profiles: dict[str, Profile]) -> list[dict]:
    desk = [p for p in profiles.values() if p.kind == "desktop"]
    out = []
    for w in list_windows():
        matched = [p.id for p in desk if window_matches(p, w)]
        if matched:
            out.append({"kind": "desktop", "target": str(w["hwnd"]), "short": str(w["hwnd"]), "title": w["title"],
                        "url": w["process"], "profiles": matched})
    return out


def is_window(hwnd: int) -> bool:
    return bool(user32.IsWindow(hwnd))


def is_minimized(hwnd: int) -> bool:
    return bool(user32.IsIconic(hwnd))


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def get_foreground() -> int:
    return int(user32.GetForegroundWindow() or 0)


def set_foreground(hwnd: int) -> bool:
    if not hwnd:
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    fg = user32.GetForegroundWindow()
    fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    my_tid = kernel32.GetCurrentThreadId()
    attached = bool(fg_tid and fg_tid != my_tid and user32.AttachThreadInput(my_tid, fg_tid, True))
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(my_tid, fg_tid, False)
    time.sleep(0.15)
    return get_foreground() == hwnd


def user_idle_seconds() -> float:
    info = _LASTINPUTINFO(cbSize=ctypes.sizeof(_LASTINPUTINFO))
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return 1e9
    return ((kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0


def cursor_pos() -> tuple[int, int]:
    p = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


# ---------------------------------------------------------------- 截图与哈希（mss + Pillow，可选依赖）

def grab(box: tuple[int, int, int, int]):
    """截取屏幕区域 (left, top, right, bottom)，返回 PIL.Image。"""
    import mss
    from PIL import Image
    left, top, right, bottom = box
    with mss.mss() as sct:
        shot = sct.grab({"left": left, "top": top, "width": max(1, right - left), "height": max(1, bottom - top)})
        return Image.frombytes("RGB", shot.size, shot.rgb)


def png_bytes(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def ahash(img, size: int = 16) -> str:
    """平均哈希，用于判断按钮区是否处于“空闲态”。"""
    g = img.convert("L").resize((size, size))
    px = list(g.getdata())
    avg = sum(px) / len(px)
    bits = "".join("1" if p > avg else "0" for p in px)
    return f"{int(bits, 2):0{size * size // 4}x}"


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def content_fingerprint(img) -> str:
    """回答区内容指纹：灰度缩小 + 量化，过滤细微渲染噪声，文字一变指纹就变。"""
    g = img.convert("L").resize((128, max(16, int(128 * img.height / max(1, img.width)))))
    q = bytes(p // 24 for p in g.getdata())
    return hashlib.md5(q).hexdigest()


def rel_box(hwnd: int, rel: list[int]) -> tuple[int, int, int, int]:
    left, top, _, _ = window_rect(hwnd)
    return left + rel[0], top + rel[1], left + rel[2], top + rel[3]


# ---------------------------------------------------------------- 剪贴板与按键（uiautomation 提供）

def clipboard_get() -> str:
    try:
        import uiautomation as auto
        return auto.GetClipboardText() or ""
    except Exception:
        return ""


def clipboard_set(text: str) -> None:
    import uiautomation as auto
    auto.SetClipboardText(text)


def send_keys(keys: str) -> None:
    import uiautomation as auto
    auto.SendKeys(keys, interval=0.01, waitTime=0.05)


def click(x: int, y: int) -> None:
    import uiautomation as auto
    auto.Click(x, y, waitTime=0.1)
