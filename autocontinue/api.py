"""本地 HTTP API（默认只监听 127.0.0.1），供天枢、MCP 桥或脚本远程控制。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import SessionSpec
from .manager import Manager


def create_app(manager: Manager):
    from fastapi import FastAPI, HTTPException, Request

    from . import __version__
    app = FastAPI(title="续推运行器 AutoContinue Runner", version=__version__)

    @app.middleware("http")
    async def protect_local_write(request: Request, call_next):
        # Local web pages must not be able to submit blind cross-origin POSTs
        # to this localhost control API. Native clients without Origin still work.
        from fastapi.responses import JSONResponse
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin:
                from urllib.parse import urlsplit
                try:
                    source = urlsplit(origin)
                    host = request.url.hostname or ""
                    allowed = (source.scheme == request.url.scheme and
                               source.hostname == host and
                               source.port == request.url.port)
                except ValueError:
                    allowed = False
                if not allowed:
                    return JSONResponse({"detail": "Cross-origin writes are blocked"}, status_code=403)
        return await call_next(request)


    def _status_or_archived(sid: str) -> dict:
        try:
            return manager.get(sid).status()
        except KeyError:
            a = manager.find_archived(sid)
            if a is None:
                raise HTTPException(404, f"没有找到会话 {sid}")
            return a

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True, "sessions": len(manager.sessions), "archived": len(manager.archive)}

    @app.get("/profiles")
    async def profiles() -> list[dict]:
        manager.reload_profiles()
        return [{"id": p.id, "name": p.name, "kind": p.kind, "verified": p.verified,
                 "url_prefix": p.match.url_prefix, "url_contains": p.match.url_contains}
                for p in manager.profiles.values()]

    @app.get("/targets")
    async def targets() -> list[dict]:
        manager.reload_profiles()
        return await manager.list_targets()

    @app.get("/sessions")
    async def sessions() -> list[dict]:
        return manager.statuses()

    @app.post("/sessions")
    async def create(spec: SessionSpec) -> dict:
        manager.reload_profiles()
        try:
            s = await manager.start(spec)
        except (ValueError, RuntimeError, ConnectionError) as e:
            raise HTTPException(400, str(e))
        return s.status()

    @app.get("/archive")
    async def archive(limit: int = 50) -> list[dict]:
        """已结束会话的摘要（最新在前）。完整记录在各自的 output 目录里。"""
        return list(manager.archive)[:max(0, limit)]

    @app.get("/sessions/{sid}")
    async def get(sid: str) -> dict:
        return _status_or_archived(sid)

    @app.post("/sessions/{sid}/{action}")
    async def act(sid: str, action: str) -> dict:
        if action not in ("pause", "resume", "step", "stop"):
            raise HTTPException(400, f"未知操作 {action}")
        try:
            s = manager.get(sid)
        except KeyError:
            a = manager.find_archived(sid)
            if a is None:
                raise HTTPException(404, f"没有找到会话 {sid}")
            if action == "stop":
                return a
            raise HTTPException(409, f"会话 {a.get('id')} 已结束并归档，记录在 {a.get('output_dir')}。"
                                     "如需继续，请在对应页面重新新建会话。")
        await getattr(s, action)()
        return s.status()

    @app.post("/sessions/{sid}/limits")
    async def limits(sid: str, body: dict) -> dict:
        """运行中调整上限：max_rounds / max_turn_min（分钟）/ max_runtime_h（小时），只改传了的项。"""
        try:
            s = manager.get(sid)
        except KeyError:
            raise HTTPException(404 if manager.find_archived(sid) is None else 409, f"会话 {sid} 不在运行中")
        def num(k):
            v = body.get(k)
            try:
                v = float(v) if v not in (None, "") else None
            except (TypeError, ValueError):
                raise HTTPException(400, f"{k} 不是数字")
            if v is not None and v <= 0:
                raise HTTPException(400, f"{k} 必须大于 0")
            return v
        return s.set_limits(num("max_rounds"), num("max_turn_min"), num("max_runtime_h"))

    @app.get("/sessions/{sid}/log")
    async def log(sid: str, tail: int = 20) -> dict[str, Any]:
        st = _status_or_archived(sid)
        d = Path(st["output_dir"])
        rounds = _tail_jsonl(d / "rounds.jsonl", tail)
        events = _tail_jsonl(d / "events.log", tail)
        return {"status": st, "rounds": rounds, "events": events}

    @app.post("/pause_all")
    async def pause_all() -> list[dict]:
        await manager.pause_all()
        return manager.statuses()

    @app.post("/resume_all")
    async def resume_all() -> list[dict]:
        await manager.resume_all()
        return manager.statuses()

    @app.post("/stop_all")
    async def stop_all() -> list[dict]:
        for s in list(manager.sessions.values()):
            await s.stop()
        return manager.statuses()

    @app.get("/events")
    async def events(after: int = 0) -> list[dict]:
        return [e for e in manager.events if e["seq"] > after]

    @app.post("/shutdown")
    async def shutdown() -> dict:
        """结束所有会话并退出运行器进程（控制页“关闭运行器”按钮）。"""
        import asyncio
        import os

        async def bye():
            await asyncio.sleep(0.3)
            try:
                await asyncio.wait_for(manager.shutdown(), 6)
            finally:
                os._exit(0)

        asyncio.get_running_loop().create_task(bye())
        return {"ok": True, "message": "运行器正在退出"}

    @app.get("/")
    async def control_page():
        from fastapi.responses import HTMLResponse
        page = Path(__file__).resolve().parent / "ui" / "control.html"
        return HTMLResponse(page.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})

    return app


def _tail_jsonl(p: Path, n: int) -> list[dict]:
    if not p.exists():
        return []
    lines = p.read_text(encoding="utf-8").splitlines()[-n:]
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except Exception:
            continue
    return out


async def serve_api(manager: Manager, host: str, port: int) -> None:
    import uvicorn
    server = uvicorn.Server(uvicorn.Config(create_app(manager), host=host, port=port, log_level="warning"))
    await server.serve()
