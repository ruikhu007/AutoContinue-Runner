"""桌面 C 档：视觉兜底。适用于自绘界面、无法用 CDP 或 UIA 读取的应用。

- 停止检测：回答区截图指纹稳定 + 按钮区与校准时的“空闲态”哈希一致。
- 判定：对回答区末尾做一次 OCR（需安装 rapidocr_onnxruntime），读取协议标记。
- 注入：切到目标窗口 → 点击输入框 → 剪贴板粘贴 → 回车或点击按钮 → 还原前台窗口与剪贴板。
"""
from __future__ import annotations

from typing import Optional

from ..config import Profile
from ..core.signals import Signals
from . import desktop_common as dc
from .base import InjectError
from .desktop_base import DesktopAdapterBase

_ocr_engine = None


def _ocr(img) -> str:
    global _ocr_engine
    try:
        if _ocr_engine is None:
            from rapidocr_onnxruntime import RapidOCR
            _ocr_engine = RapidOCR()
        import numpy as np
        result, _ = _ocr_engine(np.array(img))
    except ImportError:
        return ""
    if not result:
        return ""
    # result: [[box, text, score], ...]；按行（y）再按列（x）排序
    rows = sorted(result, key=lambda r: (round(r[0][0][1] / 12), r[0][0][0]))
    lines: list[str] = []
    last_y = None
    for box, text, _score in rows:
        y = box[0][1]
        if last_y is not None and abs(y - last_y) < 12 and lines:
            lines[-1] += " " + text
        else:
            lines.append(text)
        last_y = y
    return "\n".join(lines)


class VisionAdapter(DesktopAdapterBase):
    steals_focus = True

    def __init__(self, profile: Profile, target: Optional[str]):
        super().__init__(profile, target)
        if profile.regions is None:
            raise ValueError(f"画像 {profile.id} 缺少 regions 段，请先运行 calibrate-desktop")
        self.reg = profile.regions

    def _observe_sync(self) -> Signals:
        problem = self._window_problem()
        if problem:
            return Signals(page_ok=False, page_problem=problem)
        reply_img = dc.grab(dc.rel_box(self.hwnd, self.reg.reply))
        gen = None
        if self.reg.idle_button_hash:
            btn = dc.grab(dc.rel_box(self.hwnd, self.reg.button))
            gen = dc.hamming(dc.ahash(btn), self.reg.idle_button_hash) > self.reg.max_hash_distance
        return Signals(generating=gen, reply_hash=dc.content_fingerprint(reply_img))

    def _read_sync(self) -> str:
        img = dc.grab(dc.rel_box(self.hwnd, self.reg.reply))
        # 只识别下半部分：最新回答的末尾与协议标记都在那里
        h = img.height
        return _ocr(img.crop((0, h // 2, img.width, h)))

    def _inject_sync(self, text: str) -> None:
        def act():
            x0, y0, x1, y1 = dc.rel_box(self.hwnd, self.reg.input)
            dc.click((x0 + x1) // 2, (y0 + y1) // 2)
            dc.send_keys("{Ctrl}a")
            dc.clipboard_set(text)
            dc.send_keys("{Ctrl}v")
            self._submit_sync()
        self._with_focus(act)

    def _submit_sync(self) -> None:
        if self.profile.submit == "click":
            bx0, by0, bx1, by1 = dc.rel_box(self.hwnd, self.reg.button)
            dc.click((bx0 + bx1) // 2, (by0 + by1) // 2)
        else:
            dc.send_keys("{Ctrl}{Enter}" if self.profile.submit == "ctrl_enter" else "{Enter}")

    async def attach(self) -> str:
        return await self._run(self._bind)

    async def observe(self) -> Signals:
        return await self._run(self._observe_sync)

    async def read_last_reply(self) -> str:
        return await self._run(self._read_sync)

    async def inject(self, text: str) -> None:
        try:
            await self._run(self._inject_sync, text)
        except InjectError:
            raise
        except Exception as e:
            raise InjectError(f"视觉注入失败：{e}") from e


def calibrate_vision(profile_id: str, title_contains: str, name: str = "", submit: str = "enter",
                     ask=input, log=print) -> Profile:
    """命令行交互式校准：把鼠标移到各区域的角上按回车，记录相对窗口的坐标。"""
    from ..config import Match, VisionRegions
    w = next((w for w in dc.list_windows() if title_contains in w["title"]), None)
    if w is None:
        raise RuntimeError(f"没有找到标题包含“{title_contains}”的窗口")
    hwnd = w["hwnd"]
    log(f"目标窗口：{w['title']}  进程：{w['process']}")
    log("请保持目标窗口位置不动、不被遮挡，并确认当前模型处于空闲（没有在生成）。")

    def corner(label: str) -> tuple[int, int]:
        ask(f"  把鼠标移到【{label}】，然后回到本窗口按回车……")
        x, y = dc.cursor_pos()
        left, top, _, _ = dc.window_rect(hwnd)
        return x - left, y - top

    regions = {}
    for key, label in (("reply", "回答区"), ("input", "输入框"), ("button", "发送/停止按钮")):
        x0, y0 = corner(f"{label}的左上角")
        x1, y1 = corner(f"{label}的右下角")
        regions[key] = [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]
        log(f"  {label}：{regions[key]}")
    idle = dc.ahash(dc.grab(dc.rel_box(hwnd, regions["button"])))
    prof = Profile(id=profile_id, name=name or profile_id, kind="desktop", tier="vision",
                   match=Match(process=w["process"] or None, title_contains=title_contains),
                   regions=VisionRegions(**regions, idle_button_hash=idle), input_method="clipboard",
                   submit=submit, notes="视觉档画像；窗口大小改变后需要重新校准。")
    prof.timing.settle_ms = 5000
    prof.timing.poll_ms = 1000
    prof.timing.start_timeout_s = 60
    return prof
