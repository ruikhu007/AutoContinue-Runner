"""网页拾取校准：用户在页面上依次点选输入框、发送按钮、停止按钮、模型回答，程序生成稳健选择器并保存为站点画像。

页面内操作：鼠标悬停高亮 → 单击选中 → ↑ 扩大到父元素 / ↓ 缩小 → Enter 确认 → Esc 跳过本项。
"""
from __future__ import annotations

import asyncio
from typing import Optional
from urllib.parse import urlparse

from .adapters.web_cdp import OBSERVE_JS, CdpHub
from .config import Match, Profile, RunnerConfig, Timing, WebSelectors, save_profile

PICKER_JS = r"""
() => {
  if (window.__acrPick) return true;
  const DYN = /\d{4,}|^[a-f0-9]{8,}$|^(css|sc|jsx|emotion|svelte)-|^_[a-zA-Z0-9]{5,}$|[A-Za-z0-9]{22,}/;
  const dyn = (s) => !s || DYN.test(s) || s.length > 40;
  const q = (v) => v.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
  const count = (s) => { try { return document.querySelectorAll(s).length; } catch (e) { return 0; } };
  const has = (s, el) => { try { return Array.from(document.querySelectorAll(s)).includes(el); } catch (e) { return false; } };
  const uniq = (s, el) => count(s) === 1 && has(s, el);

  function candidates(el) {
    const tag = el.tagName.toLowerCase(), c = [];
    if (el.id && !dyn(el.id)) c.push('#' + CSS.escape(el.id));
    for (const a of ['data-testid', 'data-test-id', 'data-message-author-role', 'data-role', 'aria-label',
                     'name', 'placeholder', 'role']) {
      const v = el.getAttribute(a);
      if (v && !dyn(v)) c.push(`${tag}[${a}="${q(v)}"]`);
    }
    for (const a of Array.from(el.attributes)) {
      if (a.name.startsWith('data-') && !c.some(x => x.includes('[' + a.name + '=')) && a.value && !dyn(a.value))
        c.push(`${tag}[${a.name}="${q(a.value)}"]`);
    }
    if (tag.includes('-')) c.push(tag);
    const cls = Array.from(el.classList).filter(x => !dyn(x));
    for (const k of cls) c.push(`${tag}.${CSS.escape(k)}`);
    if (cls.length >= 2) c.push(`${tag}.${CSS.escape(cls[0])}.${CSS.escape(cls[1])}`);
    if (el.isContentEditable && el.getAttribute('contenteditable')) c.push(`${tag}[contenteditable="true"]`);
    return c;
  }
  function path(el) {
    const parts = [];
    while (el && el.nodeType === 1 && el !== document.body) {
      let s = el.tagName.toLowerCase();
      const sib = Array.from(el.parentElement.children).filter(x => x.tagName === el.tagName);
      if (sib.length > 1) s += `:nth-of-type(${sib.indexOf(el) + 1})`;
      parts.unshift(s); el = el.parentElement;
    }
    return 'body > ' + parts.join(' > ');
  }
  function single(el) {
    const own = candidates(el);
    for (const s of own) if (uniq(s, el)) return s;
    let p = el.parentElement, d = 0;
    while (p && p !== document.body && d < 5) {
      for (const ps of candidates(p).slice(0, 6)) for (const s of own.concat([el.tagName.toLowerCase()]).slice(0, 7)) {
        const sel = ps + ' ' + s; if (uniq(sel, el)) return sel;
      }
      p = p.parentElement; d++;
    }
    return path(el);
  }
  function multi(el, exclude) {
    // 匹配本元素及同类消息、且彼此不嵌套的选择器；同类越多越好（说明能泛化到每一条回答）。
    // exclude：另一方消息里的一段文字，匹配到含这段文字元素的选择器（例如把用户消息也算进来）一律淘汰。
    let best = null;
    const own = candidates(el);
    let p = el.parentElement, scoped = [];
    for (let d = 0; p && p !== document.body && d < 4; d++, p = p.parentElement)
      for (const ps of candidates(p).slice(0, 4)) for (const s of own.slice(0, 4)) scoped.push(ps + ' ' + s);
    for (const s of own.concat(scoped)) {
      if (!has(s, el)) continue;
      const els = Array.from(document.querySelectorAll(s));
      if (els.length > 300) continue;
      if (els.some(a => els.some(b => a !== b && a.contains(b)))) continue;
      if (exclude && els.some(a => a !== el && (a.innerText || '').includes(exclude))) continue;
      if (els.some(a => a.matches('textarea,input,[contenteditable="true"]'))) continue;
      const score = (els.length > 1 ? 0 : 1000) + s.length;
      if (!best || score < best.score) best = {s, score};
    }
    return best ? best.s : single(el);
  }

  const box = document.createElement('div');
  box.style.cssText = 'position:fixed;z-index:2147483646;pointer-events:none;border:2px solid #e8590c;' +
                      'background:rgba(232,89,12,.12);border-radius:4px;display:none';
  const banner = document.createElement('div');
  banner.style.cssText = 'position:fixed;z-index:2147483647;left:50%;top:12px;transform:translateX(-50%);' +
    'background:#1f2933;color:#fff;font:14px/1.5 sans-serif;padding:10px 16px;border-radius:8px;' +
    'box-shadow:0 4px 16px rgba(0,0,0,.3);max-width:80vw;display:none;pointer-events:none;opacity:.92';
  document.documentElement.append(box, banner);
  const show = (el) => { if (!el) { box.style.display = 'none'; return; } const r = el.getBoundingClientRect();
    Object.assign(box.style, {display: 'block', left: r.left + 'px', top: r.top + 'px', width: r.width + 'px', height: r.height + 'px'}); };

  window.__acrPick = (prompt, mode, exclude) => new Promise((resolve) => {
    let locked = null, chain = [];
    const tip = (extra) => { banner.innerHTML = '<b>续推运行器 · 校准</b>　' + prompt +
      '<br><span style="opacity:.75">单击选中 · ↑ 扩大 · ↓ 缩小 · Enter 确认 · Esc 跳过</span>' + (extra || ''); banner.style.display = 'block'; };
    tip();
    const over = (e) => { if (!locked) show(e.target); };
    const click = (e) => {
      if (banner.contains(e.target)) return;
      e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation();
      locked = e.target; chain = []; show(locked);
      tip('<br>已选中 &lt;' + locked.tagName.toLowerCase() + '&gt;，按 Enter 确认');
    };
    const stopEv = (e) => { if (!banner.contains(e.target)) { e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation(); } };
    const key = (e) => {
      if (e.key === 'Escape') { finish(null); }
      else if (e.key === 'ArrowUp' && locked && locked.parentElement && locked.parentElement !== document.body) {
        chain.push(locked); locked = locked.parentElement; show(locked);
      } else if (e.key === 'ArrowDown' && chain.length) { locked = chain.pop(); show(locked); }
      else if (e.key === 'Enter' && locked) { finish(locked); }
      else return;
      e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation();
    };
    const evs = [['mouseover', over], ['click', click], ['mousedown', stopEv], ['mouseup', stopEv],
                 ['pointerdown', stopEv], ['pointerup', stopEv], ['keydown', key]];
    evs.forEach(([n, f]) => window.addEventListener(n, f, true));
    function finish(el) {
      evs.forEach(([n, f]) => window.removeEventListener(n, f, true));
      show(null); banner.style.display = 'none';
      if (!el) return resolve(null);
      const s = mode === 'multi' ? multi(el, exclude) : single(el);
      resolve({selector: s, count: count(s), tag: el.tagName.toLowerCase(), text: (el.innerText || '').slice(0, 60)});
    }
  });
  window.__acrToast = (msg) => { banner.innerHTML = '<b>续推运行器 · 校准</b>　' + msg; banner.style.display = 'block'; };
  return true;
}
"""

TEST_PROMPT = "请写一段 300 字左右的自我介绍，用于测试。"


async def _pick(page, prompt: str, mode: str = "single", exclude: str = "") -> Optional[dict]:
    await page.evaluate(PICKER_JS)
    return await page.evaluate("([p, m, x]) => window.__acrPick(p, m, x)", [prompt, mode, exclude])


async def calibrate_web(cfg: RunnerConfig, hub: CdpHub, target: str, profile_id: str, name: str = "",
                        log=print) -> Profile:
    page = None
    for tid, p in await hub.pages():
        if tid.startswith(target) or target in p.url:
            page = p
            break
    if page is None:
        raise RuntimeError(f"找不到标签页 {target}")
    await page.bring_to_front()
    log(f"校准页面：{await page.title()} — {page.url}")
    log("请切换到浏览器窗口，按页面顶部的提示操作。")

    inp = await _pick(page, "第 1 步：点击<b>输入框</b>")
    if not inp:
        raise RuntimeError("必须选择输入框")
    log(f"  输入框：{inp['selector']}")

    # 写入测试文字，让发送按钮出现 / 启用
    await page.evaluate("(s) => { const el = document.querySelector(s); el && el.focus(); }", inp["selector"])
    await page.keyboard.insert_text(TEST_PROMPT)
    send = await _pick(page, "第 2 步：点击<b>发送按钮</b>（只是选中，不会真的发送）")
    log(f"  发送按钮：{send['selector'] if send else '（跳过，将用回车提交）'}")

    # 真正发送测试消息
    if send:
        try:
            await page.locator(send["selector"]).first.click(timeout=3000)
        except Exception:
            await page.keyboard.press("Enter")
    else:
        await page.keyboard.press("Enter")
    stop = await _pick(page, "第 3 步：测试消息已发送。<b>趁模型还在生成</b>，点击<b>停止生成按钮</b>"
                             "（选中即可，不会真的停止；来不及就按 Esc 跳过）")
    log(f"  停止按钮：{stop['selector'] if stop else '（跳过，将只靠文本稳定判断）'}")

    await page.evaluate("(m) => window.__acrToast(m)", "等待测试回答生成完毕……")
    await _wait_quiet(page)
    reply = await _pick(page, "第 4 步：点击<b>刚才这条模型回答</b>（可按 ↑ 扩大到整条回答的外框）", "multi",
                        TEST_PROMPT[:12])
    if not reply:
        raise RuntimeError("必须选择模型回答")
    log(f"  模型回答：{reply['selector']}（页面上共 {reply['count']} 条）")
    reply_snip = (reply.get("text") or "").strip()[:10]
    user = await _pick(page, "第 5 步（可选）：点击<b>你发出的那条测试消息</b>，用于识别人类亲自发言；不需要就按 Esc",
                       "multi", reply_snip)
    log(f"  用户消息：{user['selector'] if user else '（跳过）'}")

    u = urlparse(page.url)
    prof = Profile(
        id=profile_id, name=name or profile_id, kind="web", verified=False,
        match=Match(url_prefix=f"{u.scheme}://{u.netloc}/"),
        selectors=WebSelectors(input=inp["selector"], last_reply=reply["selector"],
                               send_button=send["selector"] if send else None,
                               stop_button=stop["selector"] if stop else None,
                               user_message=user["selector"] if user else None),
        submit="click" if send else "enter", timing=Timing(),
        notes="由拾取校准生成；首次使用请观察几轮确认无误后把 verified 改为 true。",
    )
    if hub.endpoint != cfg.cdp_endpoint:      # Electron 桌面应用等独立调试端口
        prof.cdp_endpoint = hub.endpoint
    sig = await page.evaluate(OBSERVE_JS, prof.selectors.model_dump())
    log(f"自检：找到输入框={sig['inputFound']}，回答条数={sig['replyCount']}，"
        f"生成中={sig['generating']}，最后回答前 30 字：{(sig['replyText'] or '')[:30]!r}")
    path = save_profile(cfg, prof)
    await page.evaluate("(m) => window.__acrToast(m)", f"校准完成，画像已保存：{path.name}")
    log(f"画像已保存：{path}")
    return prof


async def _wait_quiet(page, quiet_s: float = 3.0, timeout_s: float = 180) -> None:
    """等页面文本连续 quiet_s 秒不变。"""
    last, same, waited = None, 0.0, 0.0
    while waited < timeout_s:
        txt = await page.evaluate("() => document.body.innerText.length")
        same = same + 0.5 if txt == last else 0.0
        if same >= quiet_s:
            return
        last = txt
        await asyncio.sleep(0.5)
        waited += 0.5
