"""会话管理器：创建 / 查询 / 控制多个续推会话，供命令行、控制台窗口、HTTP API 共用。"""
from __future__ import annotations

import asyncio
import json
import sys
import uuid
from collections import deque
from typing import Optional

from .adapters.base import Adapter
from .adapters.base import PageUnresponsiveError
from .adapters.web_cdp import CdpHub, WebAdapter, list_web_targets, profiles_for_url, same_page_url
from .config import Profile, RunnerConfig, SessionSpec, load_profiles, load_runner_config
from .core.session import TERMINAL, Session
from .notify import Notifier
from .recorder import SessionRecorder


class Manager:
    def __init__(self, cfg: Optional[RunnerConfig] = None):
        self.cfg = cfg or load_runner_config()
        self.profiles: dict[str, Profile] = load_profiles(self.cfg)
        self.notifier = Notifier(self.cfg.notify)
        self.events: deque[dict] = deque(maxlen=300)    # 最近的通知，供网页控制页显示
        self._ev_seq = 0
        self.notifier.subscribe(self._keep_event)
        self.sessions: dict[str, Session] = {}          # 只放活动中的会话
        # 已结束的会话：只留一行摘要（不再占页面、不再计时），完整记录在 output/ 里
        self.archive: deque[dict] = deque(maxlen=300)
        self._load_archive()
        self._start_lock = asyncio.Lock()
        self._hubs: dict[str, CdpHub] = {}
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    def _keep_event(self, payload: dict) -> None:
        self._ev_seq += 1
        self.events.append({"seq": self._ev_seq, **payload})

    # ---------------------------------------------------------------- 历史记录
    def _load_archive(self, limit: int = 100) -> None:
        """启动时从 output/*/session.json 恢复最近结束的会话摘要。"""
        try:
            root = self.cfg.resolve(self.cfg.output_dir)
            metas = sorted(root.glob("*/session.json"), key=lambda p: p.parent.name, reverse=True)[:limit]
        except Exception:
            return
        for mp in metas:
            try:
                m = json.loads(mp.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not m.get("ended_at"):
                continue
            spec = m.get("spec") or {}
            self.archive.append({
                "id": m.get("session_id", ""), "name": m.get("name", ""), "profile": m.get("profile", ""),
                "target": m.get("target", ""), "state": m.get("state", ""), "paused": False,
                "rounds": m.get("rounds", 0), "max_rounds": spec.get("max_rounds") or 0,
                "task_mode": m.get("task_mode") or spec.get("task_mode") or "steps",
                "progress": m.get("progress", ""), "runtime_s": m.get("runtime_s"),
                "last_reason": m.get("reason", ""), "last_rule": m.get("last_rule", ""),
                "output_dir": str(mp.parent), "ended": True, "archived": True,
                "started_at": m.get("started_at"), "ended_at": m.get("ended_at")})

    def _archive_session(self, sid: str) -> None:
        s = self.sessions.pop(sid, None)
        if s is None:
            return
        st = s.status()
        st.update(ended=True, archived=True, started_at=s.rec.meta.get("started_at"),
                  ended_at=s.rec.meta.get("ended_at"))
        self.archive.appendleft(st)

    def find_archived(self, sid: str) -> Optional[dict]:
        hits = [a for a in self.archive if a.get("id") == sid] or \
               [a for a in self.archive if sid and str(a.get("id", "")).startswith(sid)]
        return hits[0] if hits else None

    # ---------------------------------------------------------------- 画像与目标
    def reload_profiles(self) -> None:
        self.profiles = load_profiles(self.cfg)

    def hub(self, endpoint: Optional[str] = None) -> CdpHub:
        ep = endpoint or self.cfg.cdp_endpoint
        if ep not in self._hubs:
            self._hubs[ep] = CdpHub(ep)
        return self._hubs[ep]

    async def list_targets(self) -> list[dict]:
        out: list[dict] = []
        try:
            out += await list_web_targets(self.hub(), self.profiles)
        except Exception as e:
            out.append({"kind": "web", "error": str(e)})
        if sys.platform == "win32":
            try:
                from .adapters.desktop_common import list_desktop_targets
                out += list_desktop_targets(self.profiles)
            except Exception as e:
                out.append({"kind": "desktop", "error": str(e)})
        return out

    def _make_adapter(self, profile: Profile, target: Optional[str]) -> Adapter:
        if profile.kind == "web":
            return WebAdapter(self.hub(profile.cdp_endpoint), profile, target)
        tier = profile.tier
        if tier == "auto":
            tier = ("cdp" if profile.cdp_endpoint and profile.selectors
                    else "uia" if profile.uia else "vision" if profile.regions else "")
        if tier == "cdp":
            if not profile.cdp_endpoint:
                raise ValueError(f"画像 {profile.id} 选择了 CDP 档但没有配置 cdp_endpoint")
            return WebAdapter(self.hub(profile.cdp_endpoint), profile, target)
        if sys.platform != "win32":
            raise RuntimeError("桌面 UIA / 视觉档只能在 Windows 上运行")
        if tier == "uia":
            from .adapters.desktop_uia import UIAAdapter
            return UIAAdapter(profile, target)
        if tier == "vision":
            from .adapters.desktop_vision import VisionAdapter
            return VisionAdapter(profile, target)
        raise ValueError(f"画像 {profile.id} 没有可用的接入档位，请先校准")

    # ---------------------------------------------------------------- 会话
    async def _resolve_url(self, spec: SessionSpec) -> SessionSpec:
        """spec.url：在专用浏览器里找到这个对话页面（找不到就新开），并按网址自动选画像。"""
        url = (spec.url or "").strip()
        if not url.startswith(("http://", "https://")):
            raise ValueError("网址要以 http:// 或 https:// 开头")
        pid = spec.profile_id
        if not pid:
            matched = profiles_for_url(self.profiles, url)
            if not matched:
                from urllib.parse import urlparse
                raise ValueError(f"还没有 {urlparse(url).netloc} 的站点画像，运行器不知道这个网站的输入框和回答在哪里。"
                                 "请在控制页点“校准网页”生成画像，或让云知做一个。")
            pid = matched[0]
        hub = self.hub(self.profiles[pid].cdp_endpoint if pid in self.profiles else None)
        hits = [x for x in await hub.raw_pages() if same_page_url(x["url"], url)]
        target = hits[0]["id"] if hits else (await hub.open_page(url))[0]
        return spec.model_copy(update={"profile_id": pid, "target": target})

    async def start(self, spec: SessionSpec) -> Session:
        self.loop = asyncio.get_running_loop()
        if spec.url and not spec.target:
            spec = await self._resolve_url(spec)
        elif not spec.profile_id and spec.target:
            # 从标签页选择、画像选“自动”：按这个标签页的网址选画像
            page = next((x for x in await self.hub().raw_pages() if x["id"].startswith(spec.target)), None)
            matched = profiles_for_url(self.profiles, page["url"]) if page else []
            if not matched:
                raise ValueError("这个标签页的网站还没有站点画像。请在控制页点“校准网页”生成画像，或让云知做一个。")
            spec = spec.model_copy(update={"profile_id": matched[0]})
        profile = self.profiles.get(spec.profile_id)
        if profile is None:
            raise ValueError(f"没有找到画像 {spec.profile_id}，可用：{', '.join(self.profiles) or '无'}")
        sid = uuid.uuid4().hex[:8]
        adapter = self._make_adapter(profile, spec.target)
        # 先连接目标，确认同一个标签页 / 窗口上没有正在运行的会话（两个会话互相把对方的推进词当成人类发言）
        async with self._start_lock:
            try:
                await adapter.attach()
            except PageUnresponsiveError:
                # 原标签页对自动化没有响应：在新标签页打开同一个对话再接管（对话在服务器上，内容不变）
                if profile.kind != "web" or not spec.target:
                    raise
                hub = self.hub(profile.cdp_endpoint)
                old = next((x for x in await hub.raw_pages() if x["id"].startswith(spec.target)), None)
                if not old or not old["url"].startswith(("http://", "https://")):
                    raise
                tid, _page = await hub.open_page(old["url"])
                spec = spec.model_copy(update={"target": tid})
                adapter = self._make_adapter(profile, tid)
                await adapter.attach()
            key = adapter.target_key
            if key:
                for other in self.sessions.values():
                    if other.state not in TERMINAL and other.adapter.target_key == key:
                        await adapter.detach()
                        raise ValueError(f"这个目标上已经有续推会话在运行（{other.name}，{other.id}，"
                                         f"状态：{other.state.value}）。请先在控制页结束它，再新建。")
            name = spec.name or profile.name or profile.id
            rec = SessionRecorder(self.cfg.resolve(self.cfg.output_dir), profile.id, name,
                                  meta={"session_id": sid, "spec": spec.model_dump()})
            s = Session(sid, spec, profile, self.cfg, adapter, rec, self.notifier)
            self.sessions[sid] = s
        s.task = asyncio.create_task(s.run(), name=f"acr-{sid}")
        s.task.add_done_callback(lambda _t, _sid=sid: self._archive_session(_sid))
        return s

    def get(self, sid: str) -> Session:
        if sid in self.sessions:
            return self.sessions[sid]
        hits = [s for k, s in self.sessions.items() if k.startswith(sid)]
        if len(hits) == 1:
            return hits[0]
        raise KeyError(f"没有找到会话 {sid}")

    def statuses(self) -> list[dict]:
        return [s.status() for s in self.sessions.values()]

    async def pause(self, sid: str) -> dict:
        s = self.get(sid)
        await s.pause()
        return s.status()

    async def resume(self, sid: str) -> dict:
        s = self.get(sid)
        await s.resume()
        return s.status()

    async def step(self, sid: str) -> dict:
        s = self.get(sid)
        await s.step()
        return s.status()

    async def stop(self, sid: str) -> dict:
        s = self.get(sid)
        await s.stop()
        return s.status()

    async def pause_all(self) -> None:
        for s in list(self.sessions.values()):
            if s.state not in TERMINAL:
                await s.pause("全局热键暂停")

    async def resume_all(self) -> None:
        for s in list(self.sessions.values()):
            await s.resume()

    async def wait(self, sid: str) -> dict:
        s = self.get(sid)
        if s.task:
            await asyncio.shield(s.task)
        return s.status()

    async def shutdown(self) -> None:
        for s in list(self.sessions.values()):
            await s.stop()
        tasks = [s.task for s in list(self.sessions.values()) if s.task and not s.task.done()]
        if tasks:
            await asyncio.wait(tasks, timeout=5)
        for h in self._hubs.values():
            await h.close()
