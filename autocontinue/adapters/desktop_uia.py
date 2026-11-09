"""桌面 B 档：UI Automation 控件树。读取消息区文本与停止 / 发送按钮状态，用 ValuePattern / InvokePattern 写入与提交。

控件的定位条件写在画像的 uia 段里；可用 `python -m autocontinue dump-uia --title 关键字` 打印控件树来编写。
"""
from __future__ import annotations

import hashlib
import re
import time
from typing import Optional

from ..config import Profile, UIASpec
from ..core.signals import Signals
from . import desktop_common as dc
from .base import InjectError
from .desktop_base import DesktopAdapterBase

_TEXT_TYPES = {"TextControl", "DocumentControl", "EditControl", "ListItemControl", "HyperlinkControl",
               "CustomControl", "GroupControl"}


def _norm(s: Optional[str]) -> str:
    return re.sub(r"\s+", "", s or "")


class UIAAdapter(DesktopAdapterBase):
    def __init__(self, profile: Profile, target: Optional[str]):
        super().__init__(profile, target)
        if profile.uia is None:
            raise ValueError(f"画像 {profile.id} 缺少 uia 段")
        self.spec = profile.uia
        self.steals_focus = profile.input_method != "uia_value"
        self._root = None
        self._base_text = ""

    # ---------------------------------------------------------------- 线程内同步实现
    def _find(self, spec: Optional[UIASpec]):
        if spec is None or self._root is None:
            return None
        import uiautomation as auto
        props = {}
        if spec.control_type:
            props["ControlType"] = getattr(auto.ControlType, f"{spec.control_type}Control")
        for k, attr in (("name", "Name"), ("sub_name", "SubName"), ("automation_id", "AutomationId"),
                        ("class_name", "ClassName")):
            v = getattr(spec, k)
            if v:
                props[attr] = v
        c = auto.Control(searchFromControl=self._root, searchDepth=spec.depth or 0xFFFFFFFF,
                         foundIndex=spec.found_index, **props)
        return c if c.Exists(0.3, 0.1) else None

    @staticmethod
    def _visible(c) -> bool:
        try:
            return c is not None and not c.IsOffscreen
        except Exception:
            return False

    @staticmethod
    def _area_text(c) -> str:
        import uiautomation as auto
        try:
            tp = c.GetPattern(auto.PatternId.TextPattern)
            if tp:
                return tp.DocumentRange.GetText(-1) or ""
        except Exception:
            pass
        parts: list[str] = []
        for ctrl, _depth in auto.WalkControl(c, includeTop=False, maxDepth=15):
            try:
                if ctrl.ControlTypeName in _TEXT_TYPES and ctrl.Name:
                    parts.append(ctrl.Name)
            except Exception:
                continue
        return "\n".join(parts)

    @staticmethod
    def _value(c) -> Optional[str]:
        if c is None:
            return None
        try:
            import uiautomation as auto
            vp = c.GetPattern(auto.PatternId.ValuePattern)
            if vp:
                return vp.Value
        except Exception:
            pass
        try:
            return c.Name
        except Exception:
            return None

    def _attach_sync(self) -> str:
        import uiautomation as auto
        desc = self._bind()
        self._root = auto.ControlFromHandle(self.hwnd)
        return desc

    def _observe_sync(self) -> Signals:
        problem = self._window_problem()
        if problem:
            return Signals(page_ok=False, page_problem=problem)
        area = self._find(self.spec.reply_area)
        full = self._area_text(area) if area is not None else ""
        reply = full[len(self._base_text):] if self._base_text and full.startswith(self._base_text) else full[-4000:]
        gen = None
        if self.spec.stop_button is not None:
            gen = self._visible(self._find(self.spec.stop_button))
        inp = self._find(self.spec.input)
        sig = Signals(generating=gen, reply_text=reply.strip(), reply_hash=hashlib.md5(full.encode()).hexdigest(),
                      input_text=self._value(inp))
        if area is None:
            sig.extra["missing"] = "reply_area"
        if inp is None:
            sig.page_ok = False
            sig.page_problem = "找不到输入框控件，应用界面可能已变化，请检查画像的 uia 段"
        return sig

    def _inject_sync(self, text: str) -> None:
        inp = self._find(self.spec.input)
        if inp is None:
            raise InjectError("找不到输入框控件")
        area = self._find(self.spec.reply_area)
        self._base_text = self._area_text(area) if area is not None else ""
        head = _norm(text)[:24]

        written = False
        if self.profile.input_method == "uia_value":
            try:
                import uiautomation as auto
                vp = inp.GetPattern(auto.PatternId.ValuePattern)
                vp.SetValue(text)
                time.sleep(0.2)
                written = head in _norm(self._value(inp))
            except Exception:
                written = False

        if written:
            if self._submit_without_focus():
                return

        def act():
            if not written:
                inp.SetFocus()
                dc.send_keys("{Ctrl}a")
                dc.clipboard_set(text)
                dc.send_keys("{Ctrl}v")
                time.sleep(0.3)
                if head not in _norm(self._value(inp)):
                    raise InjectError("推进词粘贴后未出现在输入框中")
            self._submit_with_focus(inp)

        self._with_focus(act)

    def _submit_without_focus(self) -> bool:
        btn = self._find(self.spec.send_button)
        if btn is None or self.profile.submit != "click":
            return False
        try:
            import uiautomation as auto
            ip = btn.GetPattern(auto.PatternId.InvokePattern)
            if ip:
                ip.Invoke()
                return True
        except Exception:
            pass
        return False

    def _submit_with_focus(self, inp) -> None:
        btn = self._find(self.spec.send_button) if self.profile.submit == "click" else None
        if btn is not None:
            try:
                btn.Click(simulateMove=False, waitTime=0.1)
                return
            except Exception:
                pass
        inp.SetFocus()
        dc.send_keys("{Ctrl}{Enter}" if self.profile.submit == "ctrl_enter" else "{Enter}")

    # ---------------------------------------------------------------- 适配器接口
    async def attach(self) -> str:
        return await self._run(self._attach_sync)

    async def observe(self) -> Signals:
        return await self._run(self._observe_sync)

    async def read_last_reply(self) -> str:
        return (await self.observe()).reply_text

    async def inject(self, text: str) -> None:
        await self._run(self._inject_sync, text)

    async def resubmit(self) -> bool:
        def do():
            if self._submit_without_focus():
                return True
            inp = self._find(self.spec.input)
            if inp is None:
                return False
            self._with_focus(lambda: self._submit_with_focus(inp))
            return True
        return await self._run(do)


def dump_uia_tree(title_contains: str, max_depth: int = 12, max_items: int = 600) -> str:
    """打印窗口控件树，帮助编写画像 uia 段。"""
    import uiautomation as auto
    w = next((w for w in dc.list_windows() if title_contains in w["title"]), None)
    if w is None:
        return f"没有找到标题包含“{title_contains}”的窗口"
    root = auto.ControlFromHandle(w["hwnd"])
    lines = [f"窗口：{w['title']}  进程：{w['process']}  类名：{w['class_name']}  句柄：{w['hwnd']}"]
    for i, (c, depth) in enumerate(auto.WalkControl(root, includeTop=False, maxDepth=max_depth)):
        if i >= max_items:
            lines.append("……（已截断）")
            break
        try:
            name = (c.Name or "").replace("\n", " ")[:50]
            lines.append(f"{'  ' * depth}[{depth}] {c.ControlTypeName.replace('Control', '')}"
                         f"  name={name!r}  id={c.AutomationId!r}  class={c.ClassName!r}")
        except Exception:
            continue
    return "\n".join(lines)
