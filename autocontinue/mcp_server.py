"""AI 调用接口（MCP）：让云知、莫尔等 AI 智能体经 MCP 直接使用续推运行器。

运行器（serve 或 launch）需要先在运行；本进程只是转发到本机 HTTP API，并加上授权规则：

  * AI 只能看到、控制自己经本接口启动的会话；人类在控制页启动的会话对 AI 不可见。
  * 启动时必须给出确切的对话网址（https），只用“已实测”的站点画像。
  * 恢复权限按暂停原因分级：技术性暂停（注入失败、超时、页面异常、重复、步骤倒退、提问）
    和 AI 自己的暂停，AI 可以恢复；【需要人类】、危险操作关键词、人类接管、输入框里有人类草稿、
    轮次/时长上限，只能由人类在控制页处理。每次恢复尝试都记入审计表。
  * 看到【全部完成】只说明模型自报完成（completion_marker_observed），不等于成果已验收。

  python -m autocontinue mcp                                        # stdio，本机 MCP 客户端（Claude Desktop 等）
  python -m autocontinue mcp --transport streamable-http --port 8766  # 本机 HTTP，需 Bearer 令牌（见 mcp.token）
"""
from __future__ import annotations

import json
import secrets
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

API = "http://127.0.0.1:8765"
DB: Path = Path("output") / "mcp_bridge.sqlite3"
TERMINAL = {"结束", "已停止", "出错"}

INSTRUCTIONS = (
    "续推运行器：守在网页大模型对话旁，模型停下就注入推进词，直到任务完成。"
    "只在人类授权的对话上用 relay_arm 启动，必须给出确切的对话网址。"
    "看到【全部完成】会自动停止；【需要人类】一律暂停，只能由人类在控制页恢复。"
    "relay_resume 只能恢复技术性暂停或你自己用 relay_pause 造成的暂停。提前结束用 relay_disarm。"
)

# 只能由人类恢复的暂停（按暂停类型 / 判定规则）
HUMAN_ONLY_KINDS = {
    "human": "人类已在对话中亲自发言（人类接管）",
    "input": "输入框里有人类尚未发送的内容",
    "step": "人类要求单步推进",
    "limit": "达到轮次/时长上限或多次限流（预算由人类决定）",
}
HUMAN_ONLY_RULES = {"3-human": "模型输出【需要人类】", "1-danger": "回答中出现危险操作关键词"}


class RelayError(ValueError):
    pass


def _req(method: str, path: str, body: Any = None, timeout: float = 30) -> Any:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:300]
        raise RelayError(f"运行器拒绝了请求（HTTP {e.code}）：{detail}") from e
    except urllib.error.URLError as e:
        raise RelayError(f"连接不到续推运行器（{API}）。请先双击桌面“续推运行器”启动。") from e


def _q(sid: str) -> str:
    return urllib.parse.quote(sid, safe="")


def _db() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB, timeout=5)
    db.execute("""CREATE TABLE IF NOT EXISTS owned_sessions (
        session_id TEXT PRIMARY KEY, profile_id TEXT, target_url TEXT NOT NULL, name TEXT, created_at REAL NOT NULL)""")
    db.execute("CREATE TABLE IF NOT EXISTS ai_pauses (session_id TEXT PRIMARY KEY, at REAL NOT NULL)")
    db.execute("""CREATE TABLE IF NOT EXISTS resume_audit (
        at REAL NOT NULL, session_id TEXT NOT NULL, pause_kind TEXT, last_rule TEXT,
        allowed INTEGER NOT NULL, reason TEXT)""")
    db.commit()
    return db


def _owned_ids() -> set[str]:
    db = _db()
    try:
        return {r[0] for r in db.execute("SELECT session_id FROM owned_sessions")}
    finally:
        db.close()


def _owned(sid: str) -> dict:
    if sid not in _owned_ids():
        raise RelayError("这个会话不是经 AI 接口启动的，AI 无权查看或控制。")
    return _req("GET", f"/sessions/{_q(sid)}")


def _state(s: dict) -> dict:
    """只返回控制需要的字段，不外泄本机路径。"""
    x = {k: s.get(k) for k in ("id", "name", "profile", "target", "state", "paused", "rounds", "max_rounds",
                               "max_turn_min", "max_runtime_h", "task_mode", "progress", "runtime_s",
                               "last_reason", "last_rule", "ended_at")}
    x["archived"] = bool(s.get("archived"))
    x["running"] = x.get("state") not in TERMINAL and not bool(x.get("paused"))
    x["ended"] = x.get("state") in TERMINAL
    # 只表示看到了模型自报的【全部完成】标记，不等于成果已通过验收
    x["completion_marker_observed"] = x.get("state") == "结束" and x.get("last_rule") == "2-done"
    x["outcome_verified"] = False
    return x


def _verified_profiles_for(url: str) -> list[str]:
    out = []
    for p in _req("GET", "/profiles"):
        if p.get("kind") != "web" or not p.get("verified"):
            continue
        pre, con = p.get("url_prefix"), p.get("url_contains")
        if not (pre or con):
            continue
        if pre and not url.startswith(pre):
            continue
        if con and con not in url:
            continue
        out.append(p["id"])
    return out


def _last_pause_kind(sid: str) -> str:
    lg = _req("GET", f"/sessions/{_q(sid)}/log?tail=60")
    for e in reversed(lg.get("events") or []):
        if e.get("event") == "pause":
            return e.get("kind") or ""
    return ""


# ---------------------------------------------------------------- 工具实现（与 MCP 无关，便于测试）

def relay_health() -> dict:
    return {"gateway": "ready", "backend": _req("GET", "/health"), "auto_stop_on_done": True}


def relay_list_targets() -> list:
    verified = {p["id"] for p in _req("GET", "/profiles") if p.get("verified")}
    return [{"target": x.get("target"), "title": x.get("title"), "url": x.get("url"), "kind": x.get("kind"),
             "profiles": [p for p in x.get("profiles", []) if p in verified]}
            for x in _req("GET", "/targets") if "target" in x]


def relay_status(session_id: str = "") -> Any:
    if session_id:
        return _state(_owned(session_id))
    own = _owned_ids()
    live = [_state(x) for x in _req("GET", "/sessions") if x.get("id") in own]
    try:
        ended = [_state(x) for x in _req("GET", "/archive?limit=300") if x.get("id") in own]
    except RelayError:
        ended = []
    return live + ended


def relay_arm(url: str, task: str = "", mode: str = "takeover", task_mode: str = "steps", name: str = "",
              profile_id: str = "", target: str = "", max_rounds: int = 0, max_runtime_h: float = 0,
              max_turn_min: float = 0) -> dict:
    url = (url or "").strip()
    if not url.startswith("https://"):
        raise RelayError("必须给出确切的对话网址（https:// 开头）。")
    if mode not in ("new", "takeover"):
        raise RelayError("mode 只能是 new（发送新任务）或 takeover（接管已有对话）。")
    if task_mode not in ("steps", "open"):
        raise RelayError("task_mode 只能是 steps（有限步骤）或 open（开放式）。")
    if mode == "new" and not task.strip():
        raise RelayError("mode=new 需要填写 task。")
    cap = 500 if task_mode == "open" else 40
    if max_rounds and not (1 <= max_rounds <= cap):
        raise RelayError(f"max_rounds 须在 1..{cap} 之间（task_mode={task_mode}）。")
    if max_runtime_h and not (0 < max_runtime_h <= 72):
        raise RelayError("max_runtime_h 须在 (0, 72] 小时之间。")
    if max_turn_min and not (5 <= max_turn_min <= 1440):
        raise RelayError("max_turn_min 须在 5..1440 分钟之间。")
    cands = _verified_profiles_for(url)
    if profile_id:
        if profile_id not in cands:
            raise RelayError(f"站点画像 {profile_id} 与该网址不匹配，或尚未实测（verified）。可用：{cands or '无'}")
    elif not cands:
        raise RelayError("这个网址没有已实测的站点画像，AI 接口不能启动。请人类先在控制页校准并确认。")
    else:
        profile_id = cands[0]
    if target:
        hit = [x for x in relay_list_targets() if x["target"] == target]
        if not hit or hit[0]["url"] != url:
            raise RelayError("target 与网址对不上（标签页已跳转或已关闭）。可以只给 url。")
    for x in _req("GET", "/sessions"):
        if x.get("state") not in TERMINAL and url in (x.get("target") or ""):
            raise RelayError("这个对话已经有一个进行中的续推会话。")
    spec: dict[str, Any] = {"profile_id": profile_id, "url": url, "mode": mode, "task_mode": task_mode,
                            "name": name or "AI 发起的续推任务", "on_need_human": "pause", "protocol": True}
    if target:
        spec["target"] = target
    if mode == "new":
        spec["task"] = task.strip()
    for k, v in (("max_rounds", max_rounds), ("max_runtime_h", max_runtime_h), ("max_turn_min", max_turn_min)):
        if v:
            spec[k] = v
    s = _req("POST", "/sessions", spec, timeout=60)
    sid = s.get("id")
    if not sid:
        raise RelayError("运行器没有返回会话 id。")
    db = _db()
    try:
        db.execute("INSERT OR REPLACE INTO owned_sessions VALUES(?,?,?,?,?)",
                   (sid, profile_id, url, spec["name"], time.time()))
        db.commit()
    finally:
        db.close()
    return {"session_id": sid, "status": _state(s), "auto_stop": "【全部完成】", "needs_human": "pause"}


def relay_pause(session_id: str) -> dict:
    _owned(session_id)
    r = _req("POST", f"/sessions/{_q(session_id)}/pause")
    db = _db()
    try:
        db.execute("INSERT OR REPLACE INTO ai_pauses VALUES(?,?)", (session_id, time.time()))
        db.commit()
    finally:
        db.close()
    return _state(r)


def relay_resume(session_id: str, reason: str = "") -> dict:
    s = _owned(session_id)
    if s.get("state") in TERMINAL:
        raise RelayError("会话已经结束，不能再唤醒。")
    if not s.get("paused"):
        return _state(s)
    kind, rule = _last_pause_kind(session_id), s.get("last_rule") or ""
    db = _db()
    try:
        by_ai = bool(db.execute("SELECT 1 FROM ai_pauses WHERE session_id=?", (session_id,)).fetchone())
        why: Optional[str] = None
        if kind == "manual" and not by_ai:
            why = "人类在控制页手动暂停"
        elif kind in HUMAN_ONLY_KINDS:
            why = HUMAN_ONLY_KINDS[kind]
        elif kind == "judge" and rule in HUMAN_ONLY_RULES:
            why = HUMAN_ONLY_RULES[rule]
        db.execute("INSERT INTO resume_audit VALUES(?,?,?,?,?,?)",
                   (time.time(), session_id, kind, rule, 0 if why else 1, (reason or "")[:500]))
        if not why:
            db.execute("DELETE FROM ai_pauses WHERE session_id=?", (session_id,))
        db.commit()
    finally:
        db.close()
    if why:
        raise RelayError(f"这次暂停需要人类在控制页 {API}/ 处理后恢复：{why}")
    return _state(_req("POST", f"/sessions/{_q(session_id)}/resume"))


def relay_disarm(session_id: str) -> dict:
    s = _owned(session_id)
    if s.get("state") in TERMINAL:
        return _state(s)
    return _state(_req("POST", f"/sessions/{_q(session_id)}/stop"))


def relay_wait(session_id: str, timeout_seconds: int = 12) -> dict:
    _owned(session_id)
    stop = time.monotonic() + max(0, min(15, int(timeout_seconds)))
    while True:
        s = relay_status(session_id)
        if s["ended"] or s.get("paused") or time.monotonic() >= stop:
            return s
        time.sleep(min(0.5, max(0.0, stop - time.monotonic())))


# ---------------------------------------------------------------- MCP 注册

def _make_server(host: str, port: int):
    try:
        from mcp.server.mcpserver import MCPServer  # mcp >= 2
        return MCPServer("autocontinue-relay", instructions=INSTRUCTIONS), 2
    except ImportError:
        from mcp.server.fastmcp import FastMCP     # mcp 1.x
        return FastMCP("autocontinue-relay", instructions=INSTRUCTIONS, host=host, port=port), 1


def _dump(x: Any) -> str:
    return json.dumps(x, ensure_ascii=False)


def run_mcp(api: str, transport: str = "stdio", host: str = "127.0.0.1", port: int = 8766, serve: bool = True,
            db_path: Optional[str | Path] = None, token_path: Optional[str | Path] = None):
    """serve=False 时只注册工具并返回服务器对象（测试用）。"""
    global API, DB
    API = api.rstrip("/")
    if db_path:
        DB = Path(db_path)
    server, major = _make_server(host, port)

    tools = {
        "relay_health": (relay_health, "只读：运行器与 AI 接口是否就绪。"),
        "relay_list_targets": (relay_list_targets, "只读：专用浏览器里已打开的对话页，以及可用（已实测）的站点画像。"),
        "relay_status": (relay_status, "只读：你经本接口启动的会话状态；不给 session_id 则列出全部（含已结束）。"
                                       "completion_marker_observed 只表示模型自报【全部完成】，outcome_verified 恒为 false。"),
        "relay_arm": (relay_arm, "在人类授权的对话上启动续推。url：确切的对话网址（专用浏览器里没打开会自动打开）。"
                                 "mode=takeover 接管已有对话（不发新任务）；mode=new 先发送 task。"
                                 "task_mode=steps 有限步骤（模型列出 N 步，每步写【步骤完成 n/N】，最多 40 轮）；"
                                 "task_mode=open 开放式（不知道总步数，每轮写【本轮完成】，最多 500 轮 / 72 小时）。"
                                 "max_turn_min：单轮最长时间（分钟，5..1440，0 用站点默认）；超过后只要模型仍在输出就继续等。"
                                 "看到【全部完成】自动停止；【需要人类】一律暂停等人类。"),
        "relay_pause": (relay_pause, "暂停你启动的会话。"),
        "relay_resume": (relay_resume, "恢复你启动的、已暂停的会话。只允许技术性暂停（注入失败/超时/页面异常/重复/步骤倒退/提问）"
                                       "或你自己 relay_pause 的暂停；【需要人类】、危险关键词、人类接管、人类草稿、轮次/时长上限"
                                       "只能由人类在控制页恢复。请写简短 reason，每次尝试都会审计。"),
        "relay_disarm": (relay_disarm, "提前结束你启动的会话（不关闭浏览器和运行器）。"),
        "relay_wait": (relay_wait, "观察进度但不发送任何内容；每次最多等 15 秒，遇到暂停或结束立即返回。"),
    }
    _register_all(server, tools)
    if not serve:
        return server

    if transport == "stdio":
        server.run("stdio")
        return None
    # HTTP 方式：只监听本机，并要求 Bearer 令牌（令牌文件首次自动生成，不要外传）
    tp = Path(token_path) if token_path else DB.parent.parent / "mcp.token"
    if not tp.exists():
        tp.write_text(secrets.token_urlsafe(36), encoding="ascii")
    secret = tp.read_text(encoding="ascii").strip()
    print(f"AI 接口（MCP {transport}）：http://{host}:{port}/mcp  令牌文件：{tp}")
    _serve_http(server, major, transport, host, port, secret)
    return None


def _register_all(server, tools: dict) -> None:
    """统一注册：返回 JSON 文本；规则拒绝时返回 {"error": …} 而不是抛异常。"""
    import functools
    import inspect
    for name, (fn, doc) in tools.items():
        def make(f):
            @functools.wraps(f)
            def wrapper(*a, **kw):
                try:
                    return _dump(f(*a, **kw))
                except RelayError as e:
                    return _dump({"error": str(e)})
            wrapper.__signature__ = inspect.signature(f).replace(return_annotation=str)
            wrapper.__annotations__ = {**f.__annotations__, "return": str}
            return wrapper

        w = make(fn)
        w.__doc__ = doc
        server.tool(name=name, description=doc)(w)


def _serve_http(server, major: int, transport: str, host: str, port: int, secret: str) -> None:
    import uvicorn
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse

    class Auth(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            if not secrets.compare_digest(request.headers.get("authorization", ""), "Bearer " + secret):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            return await call_next(request)

    if transport == "sse":
        app = server.sse_app()
    else:
        app = server.streamable_http_app()
    app.add_middleware(Auth)
    uvicorn.run(app, host=host, port=port, log_level="warning")
