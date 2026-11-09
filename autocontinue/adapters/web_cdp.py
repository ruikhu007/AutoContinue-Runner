"""网页端适配器：通过 Chrome DevTools 协议（CDP）附着到用户已登录的浏览器。

判定“生成是否结束”按可靠程度分三层，越靠前越优先：
  1. 网络流层：监听生成接口的流式请求（如 ChatGPT 的 POST /backend-api/f/conversation，text/event-stream），
     请求在途 = 正在生成；请求关闭 = 流式输出结束。与页面是否渲染完、是否虚拟化无关。
  2. 数据层：流结束后，用页面自己的登录态读取会话数据（如 ChatGPT 的 /backend-api/conversation/{id}），
     确认最新一条是“新的、已完成的助手消息”，并取得完整原文用于判定。
  3. 渲染层（兜底）：停止按钮、回答文本稳定。只在前两层不可用的站点上使用。

输入定位只认“对话输入区”：候选元素必须位于 composer_root 内，且不在任何消息 / 回答内容里，
选中后打上 data-acr-input 标记，此后写入、读取、清空都只针对这个标记元素。
"""
from __future__ import annotations

import asyncio
import re
import time
from collections import deque
from typing import Any, Optional

from ..config import Profile
from ..core.signals import Signals
from .base import Adapter, InjectError, InputBusyError, PageUnresponsiveError

# 在页面内选定唯一的输入框并打标记；OBSERVE_JS 与注入前都会调用。
PICK_INPUT_FN = r"""
const __acrPick = (sel) => {
  const all = (s) => { if (!s) return []; try { return Array.from(document.querySelectorAll(s)); } catch (e) { return []; } };
  const visible = (el) => !!el && el.isConnected && el.getClientRects().length > 0 &&
                          getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none';
  const inside = (el, s) => { if (!s) return false; try { return !!el.closest(s); } catch (e) { return false; } };
  let cands = all(sel.input).filter(visible);
  // 回答 / 用户消息 / 轮次容器里的可编辑区（如 ChatGPT 回答中的文档块）一律排除
  cands = cands.filter(e => ![sel.last_reply, sel.user_message, sel.turn_container].some(s => inside(e, s)));
  // 指定了输入区根节点时，只认根节点里面的
  if (sel.composer_root) cands = cands.filter(e => inside(e, sel.composer_root));
  const el = cands.length ? cands[cands.length - 1] : null;
  for (const x of document.querySelectorAll('[data-acr-input]')) if (x !== el) x.removeAttribute('data-acr-input');
  if (el) el.setAttribute('data-acr-input', '1');
  return el;
};
const __acrVal = (el) => !el ? null : ((el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') ? el.value : (el.innerText || el.textContent || ''));
"""

OBSERVE_JS = "(sel) => {" + PICK_INPUT_FN + r"""
  const all = (s) => { if (!s) return null; try { return Array.from(document.querySelectorAll(s)); } catch (e) { return null; } };
  const visible = (el) => !!el && el.isConnected && el.getClientRects().length > 0 &&
                          getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none';
  const anyVisible = (s) => { const l = all(s); if (l === null) return null; return l.some(visible); };
  const textOf = (el) => (el.innerText !== undefined ? el.innerText : el.textContent) || '';

  const replies = all(sel.last_reply) || [];
  const newest = (l) => !l || !l.length ? null : (sel.newest_first ? l[0] : l[l.length - 1]);
  const last = newest(replies);
  const replyText = last ? textOf(last) : '';

  let doneMarker = null;
  if (sel.reply_done_marker) {
    doneMarker = false;
    if (last) {
      const scope = (sel.turn_container && last.closest(sel.turn_container)) || last.parentElement || last;
      try { doneMarker = Array.from(scope.querySelectorAll(sel.reply_done_marker)).some(visible); } catch (e) { doneMarker = null; }
    }
  }
  const inp = __acrPick(sel);
  const users = sel.user_message ? all(sel.user_message) : null;
  const lastUser = users && users.length ? textOf(newest(users)).slice(0, 600) : null;
  const alerts = Array.from(document.querySelectorAll('[role="alert"],[aria-live="assertive"]'))
                   .filter(visible).map(textOf).join('\n').slice(0, 500);
  return {
    generating: sel.stop_button ? anyVisible(sel.stop_button) : null,
    doneMarker, replyText, replyCount: replies.length,
    userCount: users === null ? null : users.length, lastUser,
    inputFound: !!inp, inputText: __acrVal(inp), alerts, url: location.href, title: document.title,
  };
}"""

# 重新选定输入框，聚焦并选中其中已有内容（随后 insertText 整体替换）。只作用于 data-acr-input。
FOCUS_JS = "(sel) => {" + PICK_INPUT_FN + r"""
  const el = __acrPick(sel);
  if (!el) return false;
  el.scrollIntoView({block: 'nearest'});
  el.focus();
  if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') { el.select(); }
  else {
    const r = document.createRange(); r.selectNodeContents(el);
    const g = window.getSelection(); g.removeAllRanges(); g.addRange(r);
  }
  return true;
}"""

FOCUS_END_JS = r"""() => {
  const el = document.querySelector('[data-acr-input]');
  if (!el) return false;
  el.focus();
  if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') { const n = el.value.length; el.setSelectionRange(n, n); }
  else {
    const r = document.createRange(); r.selectNodeContents(el); r.collapse(false);
    const g = window.getSelection(); g.removeAllRanges(); g.addRange(r);
  }
  return true;
}"""

# 备选写入：原生 value setter（绕过 React 受控组件）或 execCommand（contenteditable）。
SET_VALUE_JS = r"""(text) => {
  const el = document.querySelector('[data-acr-input]');
  if (!el) return false;
  el.focus();
  if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') {
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, text);
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
  } else {
    const r = document.createRange(); r.selectNodeContents(el);
    const g = window.getSelection(); g.removeAllRanges(); g.addRange(r);
    document.execCommand('insertText', false, text);
  }
  return true;
}"""

READ_INPUT_JS = r"""() => {
  const el = document.querySelector('[data-acr-input]');
  if (!el) return null;
  return (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') ? el.value : (el.innerText || el.textContent || '');
}"""

# 返回可点击的发送按钮序号；按钮禁用返回 -2，找不到返回 -1。任何“停止”类按钮都不算发送按钮。
SEND_INDEX_JS = r"""(s) => {
  let list; try { list = Array.from(document.querySelectorAll(s)); } catch (e) { return -1; }
  const STOP = /停止|stop/i;
  let disabledSeen = false;
  for (let i = 0; i < list.length; i++) {
    const b = list[i];
    if (b.getClientRects().length === 0) continue;
    const label = (b.getAttribute('aria-label') || '') + ' ' + (b.getAttribute('data-testid') || '') + ' ' + (b.innerText || '');
    if (STOP.test(label)) continue;
    if (b.disabled || b.getAttribute('aria-disabled') === 'true') { disabledSeen = true; continue; }
    return i;
  }
  return disabledSeen ? -2 : -1;
}"""

CLICK_SEND_JS = r"""(s) => {
  let list; try { list = Array.from(document.querySelectorAll(s)); } catch (e) { return false; }
  const STOP = /停止|stop/i;
  for (const b of list) {
    if (b.getClientRects().length === 0) continue;
    const label = (b.getAttribute('aria-label') || '') + ' ' + (b.getAttribute('data-testid') || '') + ' ' + (b.innerText || '');
    if (STOP.test(label) || b.disabled || b.getAttribute('aria-disabled') === 'true') continue;
    b.click(); return true;
  }
  return false;
}"""

# ChatGPT 数据层：用页面自己的登录态读取会话，返回最新状态与完整原文。
CHATGPT_DATA_JS = r"""async () => {
  const m = location.pathname.match(/\/c\/([0-9a-f-]{36})/);
  if (!m) return {ok: false, reason: '当前页面还不是一个已保存的会话'};
  const convId = m[1];
  const now = Date.now();
  const getTok = async (force) => {
    if (!force && window.__acrTok && now - window.__acrTok.t < 10 * 60 * 1000) return window.__acrTok.v;
    const s = await (await fetch('/api/auth/session', {credentials: 'include'})).json();
    window.__acrTok = {v: s.accessToken, t: Date.now()};
    return s.accessToken;
  };
  let tok = await getTok(false);
  const get = (t) => fetch('/backend-api/conversation/' + convId, {credentials: 'include', headers: t ? {Authorization: 'Bearer ' + t} : {}});
  let r = await get(tok);
  if (r.status === 401 || r.status === 403) { tok = await getTok(true); r = await get(tok); }
  if (!r.ok) return {ok: false, reason: 'HTTP ' + r.status};
  const c = await r.json();
  const textOf = (msg) => {
    const ct = msg && msg.content;
    if (!ct) return '';
    if (Array.isArray(ct.parts)) return ct.parts.filter(p => typeof p === 'string').join('');
    if (typeof ct.text === 'string') return ct.text;
    return '';
  };
  const cur = c.mapping && c.mapping[c.current_node] && c.mapping[c.current_node].message;
  let asst = null, user = null, id = c.current_node, steps = 0;
  while (id && c.mapping[id] && steps < 200 && (!asst || !user)) {
    const msg = c.mapping[id].message;
    if (msg && msg.author) {
      if (!asst && msg.author.role === 'assistant' && textOf(msg).length > 0) asst = msg;
      if (!user && msg.author.role === 'user') user = msg;
    }
    id = c.mapping[id].parent; steps++;
  }
  return {
    ok: true, convId,
    curId: cur ? cur.id : null, curRole: cur && cur.author ? cur.author.role : null,
    curStatus: cur ? cur.status : null, curEndTurn: cur ? cur.end_turn : null,
    asstId: asst ? asst.id : null, asstText: asst ? textOf(asst) : '',
    userId: user ? user.id : null, userText: user ? textOf(user).slice(0, 600) : null,
    asyncStatus: c.async_status === undefined ? null : c.async_status,
  };
}"""

DATA_LAYERS = {"chatgpt": CHATGPT_DATA_JS}


def _norm(s: Optional[str]) -> str:
    return re.sub(r"\s+", "", s or "")


def url_matches(profile: Profile, url: str) -> bool:
    m = profile.match
    if m.url_prefix and not url.startswith(m.url_prefix):
        return False
    if m.url_contains and m.url_contains not in url:
        return False
    return True


class CdpHub:
    """一个 CDP 端点上的 Playwright 连接，多个会话共享。"""

    def __init__(self, endpoint: str):
        self.endpoint = endpoint
        self._pw = None
        self.browser = None
        self._lock = asyncio.Lock()
        self._tids: dict[int, str] = {}

    async def connect(self):
        async with self._lock:
            if self.browser is not None and self.browser.is_connected():
                return self.browser
            from playwright.async_api import async_playwright
            if self._pw is None:
                self._pw = await async_playwright().start()
            try:
                self.browser = await self._pw.chromium.connect_over_cdp(self.endpoint, timeout=15000)
            except Exception as e:
                raise ConnectionError(
                    f"无法连接浏览器调试端口 {self.endpoint}。请先双击“续推运行器”"
                    f"（或 python -m autocontinue launch）启动专用浏览器。原始错误：{e}") from e
            return self.browser

    async def target_id(self, page) -> str:
        key = id(page)
        if key not in self._tids:
            s = await page.context.new_cdp_session(page)
            try:
                info = await s.send("Target.getTargetInfo")
                self._tids[key] = info["targetInfo"]["targetId"]
            finally:
                await s.detach()
        return self._tids[key]

    async def raw_pages(self) -> list[dict]:
        """直接读浏览器的 /json/list：只要浏览器在，就能立刻列出全部页面（不依赖页面脚本是否有响应）。"""
        import json as _json
        import urllib.request

        def get() -> list[dict]:
            with urllib.request.urlopen(self.endpoint.rstrip("/") + "/json/list", timeout=8) as r:
                return _json.loads(r.read().decode("utf-8"))
        try:
            items = await asyncio.to_thread(get)
        except Exception as e:
            raise ConnectionError(
                f"无法连接浏览器调试端口 {self.endpoint}。请先双击“续推运行器”"
                f"（或 python -m autocontinue launch）启动专用浏览器。原始错误：{e}") from e
        return [{"id": x.get("id", ""), "title": x.get("title", ""), "url": x.get("url", "")}
                for x in items if x.get("type") == "page"
                and not str(x.get("url", "")).startswith(("devtools://", "chrome-extension://"))]

    async def open_page(self, url: str):
        """在专用浏览器里新开一个标签页并打开网址，返回 (target_id, page)。"""
        browser = await self.connect()
        ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = await ctx.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception:
            pass
        await asyncio.sleep(4)          # 等单页应用把对话和输入框渲染出来
        return await self.target_id(page), page

    async def pages(self) -> list[tuple[str, Any]]:
        browser = await self.connect()
        out = []
        for ctx in browser.contexts:
            for page in ctx.pages:
                if page.url.startswith(("devtools://", "chrome-extension://")):
                    continue
                try:
                    out.append((await self.target_id(page), page))
                except Exception:
                    continue
        return out

    async def close(self) -> None:
        # 只断开 Playwright，不关闭用户的浏览器
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception:
                pass
        self._pw = None
        self.browser = None


class WebAdapter(Adapter):
    def __init__(self, hub: CdpHub, profile: Profile, target: Optional[str] = None):
        if profile.selectors is None:
            raise ValueError(f"画像 {profile.id} 缺少 selectors，请先校准")
        self.hub = hub
        self.profile = profile
        self.sel = profile.selectors
        self.target = target
        self.page = None
        self.target_key: Optional[str] = None
        self._closed = False
        net = profile.network
        self._net_re = re.compile(net.stream_url_pattern) if net.stream_url_pattern else None
        self._net_method = (net.stream_method or "").upper() or None
        self._active: set = set()
        self._stream_seq = 0
        self._stream_end_t = 0.0
        self._data_js = DATA_LAYERS.get(net.data_layer or "")
        self._data: Optional[dict] = None
        self._data_t = 0.0
        self._data_fail = 0
        self._eval_fail = 0
        self._missing_input = 0
        self._last: Optional[Signals] = None
        self._listeners: list[tuple[str, Any]] = []
        self._own_texts: deque[str] = deque(maxlen=20)

    # ---------------------------------------------------------------- 连接
    async def attach(self) -> str:
        if self.page is not None and self.attached_desc:
            return self.attached_desc
        pages = await self.hub.pages()
        chosen, chosen_tid = None, None
        for tid, page in pages:
            if self.target and (tid == self.target or tid.startswith(self.target)):
                chosen, chosen_tid = page, tid
                break
        if chosen is None and self.target:
            raise RuntimeError(f"找不到标签页 {self.target}，它可能已经关闭")
        if chosen is None:
            for tid, p in pages:
                if url_matches(self.profile, p.url):
                    chosen, chosen_tid = p, tid
                    break
        if chosen is None:
            raise RuntimeError(f"浏览器中没有找到匹配画像“{self.profile.name or self.profile.id}”的标签页，请先打开它")
        # 有的标签页（例如从新标签页手动跳转过去、或被浏览器冻结过的）对自动化脚本没有响应：
        # 先限时探测，免得整个运行器被拖住
        try:
            title = await asyncio.wait_for(chosen.title(), 6)
            await asyncio.wait_for(chosen.evaluate("1"), 6)
        except Exception as e:
            raise PageUnresponsiveError(
                f"标签页 {chosen_tid[:8]} 对自动化脚本没有响应（{type(e).__name__}）。"
                "可以把这个对话的网址粘贴到控制页，运行器会在新标签页里重新打开它。") from e
        self.page = chosen
        self.target_key = f"web:{self.hub.endpoint}:{chosen_tid}"
        self._on("close", self._on_close)
        if self._net_re:
            self._on("request", self._on_request)
            self._on("requestfinished", self._on_request_done)
            self._on("requestfailed", self._on_request_done)
        self.attached_desc = f"{title} — {chosen.url}"
        return self.attached_desc

    def _on(self, event: str, fn) -> None:
        self.page.on(event, fn)
        self._listeners.append((event, fn))

    def _on_close(self, *_):
        self._closed = True

    def _is_stream(self, req) -> bool:
        if self._net_method and req.method.upper() != self._net_method:
            return False
        return bool(self._net_re.search(req.url))

    def _on_request(self, req):
        if self._is_stream(req):
            self._active.add(req)
            self._stream_seq += 1

    def _on_request_done(self, req):
        if req in self._active:
            self._active.discard(req)
            self._stream_end_t = time.monotonic()

    async def detach(self) -> None:
        if self.page is not None:
            for event, fn in self._listeners:
                try:
                    self.page.remove_listener(event, fn)
                except Exception:
                    pass
        self._listeners.clear()

    # ---------------------------------------------------------------- 数据层
    async def _fetch_data(self, force: bool = False) -> Optional[dict]:
        """读取数据层。流在途时不读；流结束后的第一次读取立即进行，其余按 data_poll_s 节流。"""
        if not self._data_js or self.page is None:
            return None
        now = time.monotonic()
        fresh_after_stream = self._data_t > self._stream_end_t
        if not force and self._active:
            return self._data   # 流在途：沿用缓存，生成状态由网络流决定
        if not force and fresh_after_stream and now - self._data_t < self.profile.timing.data_poll_s:
            return self._data
        try:
            d = await self.page.evaluate(self._data_js)
        except Exception as e:
            d = {"ok": False, "reason": f"{type(e).__name__}: {e}"}
        self._data_t = time.monotonic()
        self._data = d
        self._data_fail = 0 if d.get("ok") else self._data_fail + 1
        return d

    # ---------------------------------------------------------------- 感知
    def _selectors(self) -> dict:
        return self.sel.model_dump()

    async def observe(self) -> Signals:
        if self._closed or self.page is None or self.page.is_closed():
            return Signals(page_ok=False, page_problem="标签页已关闭")
        try:
            r = await self.page.evaluate(OBSERVE_JS, self._selectors())
            self._eval_fail = 0
        except Exception as e:
            # 页面跳转、刷新时短暂失败属正常；连续失败才算异常
            self._eval_fail += 1
            if self._eval_fail >= 20:
                return Signals(page_ok=False, page_problem=f"页面脚本连续执行失败：{e}")
            return self._last or Signals(generating=True)

        sig = Signals(
            generating=r["generating"], done_marker=r["doneMarker"],
            net_active=len(self._active) if self._net_re else None,
            stream_seq=self._stream_seq if self._net_re else None,
            reply_text=r["replyText"] or "", reply_count=r["replyCount"], user_count=r["userCount"],
            last_user_text=r.get("lastUser"),
            input_text=r["inputText"], alert=r["alerts"] or "",
            extra={"url": r["url"], "title": r["title"]},
        )

        d = await self._fetch_data()
        if d and d.get("ok"):
            done = (d.get("curRole") == "assistant" and d.get("curEndTurn") is True
                    and d.get("curStatus") == "finished_successfully")
            sig.authoritative = True
            sig.turn_done = done
            sig.msg_id = d.get("asstId")
            sig.reply_text = d.get("asstText") or ""
            sig.reply_hash = f"{d.get('asstId')}:{len(sig.reply_text)}:{d.get('curId')}"
            if d.get("userText") is not None:
                sig.last_user_text = d.get("userText")
            # 生成状态以网络流与数据层为准，不看页面渲染
            sig.generating = bool(self._active) or not done
            sig.done_marker = None
            sig.extra["data"] = {k: d.get(k) for k in ("curRole", "curStatus", "curEndTurn", "asyncStatus")}
        elif self._data_js and self._data_fail:
            sig.extra["data_problem"] = (d or {}).get("reason", "")
        if self._net_re and self._stream_end_t and not self._active:
            sig.extra["stream_idle_s"] = time.monotonic() - self._stream_end_t

        if not url_matches(self.profile, r["url"]):
            sig.page_ok = False
            sig.page_problem = f"页面已离开目标站点（{r['url'][:80]}），可能登录失效或被跳转"
        self._missing_input = 0 if r["inputFound"] else self._missing_input + 1
        if self._missing_input >= 30:
            sig.page_ok = False
            sig.page_problem = "持续找不到对话输入框，页面可能已改版，请重新校准"
        self._last = sig
        return sig

    async def read_last_reply(self) -> str:
        if self._data_js:
            d = await self._fetch_data(force=True)
            if d and d.get("ok"):
                return d.get("asstText") or ""
        sig = await self.observe()
        return sig.reply_text

    # ---------------------------------------------------------------- 注入
    async def _read_input(self) -> Optional[str]:
        try:
            return await self.page.evaluate(READ_INPUT_JS)
        except Exception:
            return None

    async def foreign_input(self) -> str:
        if self.page is None or self.page.is_closed():
            return ""
        try:
            await self.page.evaluate("(sel) => {" + PICK_INPUT_FN + " return !!__acrPick(sel); }", self._selectors())
        except Exception:
            return ""
        raw = await self._read_input() or ""
        return raw.strip() if _norm(raw) and not self._is_own(raw) else ""

    async def clear_input(self) -> bool:
        page = self.page
        try:
            if not await page.evaluate(FOCUS_JS, self._selectors()):
                return False
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Delete")
            await asyncio.sleep(0.3)
            if _norm(await self._read_input() or ""):
                await page.evaluate(SET_VALUE_JS, "")
                await asyncio.sleep(0.2)
            return not _norm(await self._read_input() or "")
        except Exception:
            return False

    def _is_own(self, text: str) -> bool:
        cur = _norm(text)
        return any(cur.startswith(_norm(t)[:16]) or _norm(t).startswith(cur) for t in self._own_texts)

    async def _write(self, text: str, method: str) -> bool:
        page = self.page
        if not await page.evaluate(FOCUS_JS, self._selectors()):
            return False
        if method == "insert_text":
            await page.keyboard.insert_text(text)
        else:
            if not await page.evaluate(SET_VALUE_JS, text):
                return False
        await asyncio.sleep(0.2)
        head = _norm(text)[:24]
        return head in _norm(await self._read_input())

    async def inject(self, text: str) -> None:
        if self.page is None or self.page.is_closed():
            raise InjectError("标签页已关闭")
        if self._active:
            raise InjectError("模型仍在流式输出，暂不注入")
        found = await self.page.evaluate("(sel) => {" + PICK_INPUT_FN + " return !!__acrPick(sel); }",
                                         self._selectors())
        if not found:
            raise InjectError("找不到对话输入框（已排除回答内容里的可编辑区）")
        existing = _norm(await self._read_input())
        if existing and not self._is_own(existing):
            raise InputBusyError("对话输入框里有尚未发送的内容（可能是人类正在输入），为避免覆盖已暂停")
        self._own_texts.append(text)
        methods = [self.profile.input_method] + [m for m in ("insert_text", "value_setter")
                                                 if m != self.profile.input_method]
        ok = False
        for m in methods:
            if m not in ("insert_text", "value_setter"):
                continue
            try:
                if await self._write(text, m):
                    ok = True
                    break
            except Exception:
                continue
        if not ok:
            raise InjectError("推进词未能写入对话输入框")
        await self._submit()

    async def _submit(self) -> None:
        page = self.page
        mode = self.profile.submit
        if mode == "click" and self.sel.send_button:
            idx = -2
            for _ in range(10):  # 部分站点要等输入事件处理完才启用发送按钮
                idx = await page.evaluate(SEND_INDEX_JS, self.sel.send_button)
                if idx >= 0:
                    break
                await asyncio.sleep(0.2)
            if idx >= 0:
                btn = page.locator(self.sel.send_button).nth(idx)
                try:
                    await btn.click(timeout=3000)
                    return
                except Exception:
                    pass
                # 点击报错时消息往往已经发出（发送按钮随即变成停止按钮）。先确认，再决定是否补一次，
                # 绝不用会等 30 秒的定位器去点一个已经消失的按钮（2026-11-08 shuna 电脑上的出错原因）
                await asyncio.sleep(0.5)
                if await page.evaluate(SEND_INDEX_JS, self.sel.send_button) < 0:
                    return
                if await page.evaluate(CLICK_SEND_JS, self.sel.send_button):
                    return
        # 找不到发送按钮或配置为按键提交
        await page.evaluate(FOCUS_END_JS)
        await page.keyboard.press("Control+Enter" if mode == "ctrl_enter" else "Enter")

    async def resubmit(self) -> bool:
        if self.page is None or self.page.is_closed() or self._active:
            return False
        await self._submit()
        return True

    async def snapshot(self) -> Optional[bytes]:
        if self.page is None or self.page.is_closed():
            return None
        try:
            return await self.page.screenshot(type="png", timeout=10000)
        except Exception:
            return None


async def list_web_targets(hub: CdpHub, profiles: dict[str, Profile]) -> list[dict]:
    out = []
    for x in await hub.raw_pages():
        out.append({"kind": "web", "target": x["id"], "short": x["id"][:8], "title": x["title"], "url": x["url"],
                    "profiles": profiles_for_url(profiles, x["url"])})
    return out


def profiles_for_url(profiles: dict[str, Profile], url: str) -> list[str]:
    return [p.id for p in profiles.values() if p.kind == "web" and p.selectors and url_matches(p, url)
            and (p.match.url_prefix or p.match.url_contains)]


def same_page_url(a: str, b: str) -> bool:
    """两个网址是否指同一个对话页面（忽略 #片段 和末尾斜杠）。"""
    def norm(u: str) -> str:
        return (u or "").split("#", 1)[0].rstrip("/")
    return bool(a) and norm(a) == norm(b)
