"""命令行入口。

  python -m autocontinue launch                      一键启动：专用浏览器 + 运行器 + 控制页（已在运行的不重复启动）
  python -m autocontinue chrome                      以调试端口启动浏览器（首次在其中登录各家大模型）
  python -m autocontinue ui                          打开控制台窗口（同时启动本地 HTTP API）
  python -m autocontinue targets                     列出可续推的标签页 / 窗口
  python -m autocontinue run --profile gemini_web --task "……"      命令行直接跑一个会话
  python -m autocontinue calibrate --target <标签页id前缀> --id my_site   拾取校准网页站点
  python -m autocontinue calibrate-desktop --title 元宝 --id yuanbao_vision   视觉档校准（Windows）
  python -m autocontinue dump-uia --title 元宝       打印桌面应用控件树（Windows）
  python -m autocontinue serve                       只启动 HTTP API（供天枢调用）
  python -m autocontinue mcp                         MCP 桥（转发到 HTTP API）
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from . import __version__
from .config import SessionSpec, load_runner_config, save_profile
from .manager import Manager


def _console_utf8() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _url_ok(url: str, timeout: float = 2.0) -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=timeout):
            return True
    except Exception:
        return False


def cmd_launch(cfg, a) -> int:
    """一键启动：只补齐没在运行的部分，最后打开控制页。可以反复点击。"""
    import subprocess
    import time
    import webbrowser
    from .chrome import launch_chrome
    from .config import app_root

    srv = cfg.server
    api = f"http://{srv.api_host}:{srv.api_port}"
    root = app_root()

    # 1. 专用浏览器（调试端口）
    if _url_ok(cfg.cdp_endpoint.rstrip("/") + "/json/version"):
        print(f"[1/3] 专用浏览器已在运行（{cfg.cdp_endpoint}）")
    else:
        urls = a.urls or ["https://chatgpt.com/"]
        print(f"[1/3] {launch_chrome(cfg, urls)}")

    # 2. 运行器后台（单独的命令行窗口，关掉它即退出运行器）
    if _url_ok(api + "/health"):
        print(f"[2/3] 运行器已在运行（{api}）")
    else:
        cfg_args = ["--config", str(Path(a.config).resolve())] if a.config else []
        if sys.platform == "win32":
            # 直接在新的命令行窗口里启动（不经 cmd 的 start），出错时窗口保留报错信息
            env = dict(os.environ, ACR_KEEP_WINDOW_ON_ERROR="1")
            subprocess.Popen([sys.executable, "-m", "autocontinue", *cfg_args, "serve"], cwd=str(root), env=env,
                             creationflags=subprocess.CREATE_NEW_CONSOLE)
        else:
            subprocess.Popen([sys.executable, "-m", "autocontinue", *cfg_args, "serve"], cwd=str(root),
                             start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        for i in range(180):                      # 新装后第一次启动较慢（杀毒软件扫描、编译缓存），最多等 90 秒
            if _url_ok(api + "/health"):
                break
            if i == 20:
                print("[2/3] 正在启动运行器，第一次启动可能需要一两分钟……")
            time.sleep(0.5)
        else:
            print("[2/3] 运行器 90 秒内没有启动成功，请查看名为 AutoContinue Runner 的窗口里的报错"
                  "（也记录在 output\\runner_error.log）。")
            return 1
        print(f"[2/3] 运行器已启动（{api}）")

    # 3. 控制页
    webbrowser.open(api + "/")
    print(f"[3/3] 已打开控制页 {api}/")
    return 0


def cmd_targets(m: Manager, a) -> int:
    async def go():
        if a.endpoint:
            from .adapters.web_cdp import list_web_targets
            rows = await list_web_targets(m.hub(a.endpoint), m.profiles)
        else:
            rows = await m.list_targets()
        await m.shutdown()
        return rows
    rows = asyncio.run(go())
    if not rows:
        print("没有找到任何目标。")
    for t in rows:
        if "error" in t:
            print(f"[{t['kind']}] 错误：{t['error']}")
            continue
        prof = ",".join(t["profiles"]) or "（无匹配画像）"
        print(f"[{t['kind']}] {t['short']:<10} {t['title'][:36]:<36}  {t['url'][:60]}\n{'':14}画像：{prof}")
    return 0


def cmd_run(m: Manager, a) -> int:
    task = Path(a.task_file).read_text(encoding="utf-8") if a.task_file else a.task
    mode = a.mode or ("new" if task else "takeover")
    spec = SessionSpec(profile_id=a.profile, target=a.target, task=task, mode=mode, name=a.name,
                       max_rounds=a.max_rounds, on_need_human=a.on_need_human)

    async def go():
        loop = asyncio.get_running_loop()
        if m.cfg.hotkeys:
            from .hotkeys import start_hotkeys
            start_hotkeys(lambda: asyncio.run_coroutine_threadsafe(m.pause_all(), loop),
                          lambda: asyncio.run_coroutine_threadsafe(m.resume_all(), loop))
        api_task = None
        if a.api:
            from .api import serve_api
            api_task = asyncio.create_task(serve_api(m, m.cfg.server.api_host, m.cfg.server.api_port))
        s = await m.start(spec)
        print(f"会话 {s.id} 已启动，记录目录：{s.rec.dir}")
        print("暂停：Ctrl+Alt+P　恢复：Ctrl+Alt+R　结束：Ctrl+C")
        try:
            while s.state.value not in ("结束", "已停止", "出错"):
                await asyncio.sleep(0.5)
        finally:
            await m.shutdown()
            if api_task:
                api_task.cancel()
        st = s.status()
        print(f"会话结束：{st['state']}，推进 {st['rounds']} 轮，进度 {st['progress'] or '—'}，原因：{st['last_reason']}")
        return 0 if st["state"] == "结束" else 1

    try:
        return asyncio.run(go())
    except KeyboardInterrupt:
        print("\n已手动结束。")
        return 130


def cmd_serve(m: Manager, a) -> int:
    from .api import serve_api
    srv = m.cfg.server
    url = f"http://{srv.api_host}:{srv.api_port}/"
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleTitleW("AutoContinue Runner")
        except Exception:
            pass
    print(f"续推运行器已启动。控制页：{url}（关闭本窗口或在控制页点“关闭运行器”即可退出）")

    async def go():
        loop = asyncio.get_running_loop()
        if m.cfg.hotkeys:
            from .hotkeys import start_hotkeys
            if start_hotkeys(lambda: asyncio.run_coroutine_threadsafe(m.pause_all(), loop),
                             lambda: asyncio.run_coroutine_threadsafe(m.resume_all(), loop)):
                print("全局热键：Ctrl+Alt+P 全部暂停，Ctrl+Alt+R 全部恢复")
        if getattr(a, "open", False):
            async def open_page():
                await asyncio.sleep(1.5)
                import webbrowser
                webbrowser.open(url)
            loop.create_task(open_page())
        try:
            await serve_api(m, srv.api_host, srv.api_port)
        finally:
            await m.shutdown()
    try:
        asyncio.run(go())
    except KeyboardInterrupt:
        pass
    return 0


def cmd_calibrate(m: Manager, a) -> int:
    from .calibrate import calibrate_web

    async def go():
        try:
            return await calibrate_web(m.cfg, m.hub(a.endpoint), a.target, a.id, a.name or "")
        finally:
            await m.shutdown()
    asyncio.run(go())
    return 0


def cmd_calibrate_desktop(m: Manager, a) -> int:
    from .adapters.desktop_vision import calibrate_vision
    prof = calibrate_vision(a.id, a.title, a.name or "", submit=a.submit)
    print(f"画像已保存：{save_profile(m.cfg, prof)}")
    return 0


def cmd_dump_uia(_m: Manager, a) -> int:
    from .adapters.desktop_uia import dump_uia_tree
    print(dump_uia_tree(a.title, max_depth=a.depth))
    return 0


def cmd_profiles(m: Manager, _a) -> int:
    for p in m.profiles.values():
        flag = "已实测" if p.verified else "未实测"
        print(f"{p.id:<20} {p.kind:<8} {flag}  {p.name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    _console_utf8()
    ap = argparse.ArgumentParser(prog="autocontinue", description=f"续推运行器 v{__version__}")
    ap.add_argument("--config", help="runner.yaml 路径（默认程序目录下）")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("launch", help="一键启动：专用浏览器 + 运行器 + 控制页")
    p.add_argument("urls", nargs="*", help="浏览器首次启动时打开的网址，默认 https://chatgpt.com/")

    p = sub.add_parser("chrome", help="以调试端口启动 Chrome / Edge")
    p.add_argument("urls", nargs="*")
    sub.add_parser("ui", help="打开控制台窗口")
    p = sub.add_parser("targets", help="列出可续推的目标")
    p.add_argument("--endpoint", help="只看某个调试端口，例如 Electron 应用的 http://127.0.0.1:9333")
    sub.add_parser("profiles", help="列出站点画像")
    p = sub.add_parser("serve", help="启动运行器后台与网页控制页（无窗口）")
    p.add_argument("--open", action="store_true", help="启动后在默认浏览器打开控制页")

    p = sub.add_parser("run", help="命令行直接运行一个续推会话")
    p.add_argument("--profile", required=True)
    p.add_argument("--target", help="标签页 id（前缀即可）或窗口句柄；不填则自动选第一个匹配的")
    p.add_argument("--task", help="任务说明（填了默认 mode=new）")
    p.add_argument("--task-file", help="从文件读取任务说明")
    p.add_argument("--mode", choices=["new", "takeover"])
    p.add_argument("--name")
    p.add_argument("--max-rounds", type=int)
    p.add_argument("--on-need-human", choices=["pause", "auto"])
    p.add_argument("--api", action="store_true", help="同时启动 HTTP API")

    p = sub.add_parser("calibrate", help="拾取校准网页站点")
    p.add_argument("--target", required=True, help="标签页 id 前缀或 URL 片段")
    p.add_argument("--id", required=True, help="画像 ID（英文）")
    p.add_argument("--name")
    p.add_argument("--endpoint", help="调试端口地址；校准 Electron 桌面应用时填写，例如 http://127.0.0.1:9333")

    p = sub.add_parser("calibrate-desktop", help="视觉档校准（Windows）")
    p.add_argument("--title", required=True, help="窗口标题关键字")
    p.add_argument("--id", required=True)
    p.add_argument("--name")
    p.add_argument("--submit", choices=["enter", "click", "ctrl_enter"], default="enter")

    p = sub.add_parser("dump-uia", help="打印桌面应用控件树（Windows）")
    p.add_argument("--title", required=True)
    p.add_argument("--depth", type=int, default=12)

    p = sub.add_parser("mcp", help="MCP 桥（转发到 HTTP API）")
    p.add_argument("--transport", choices=["stdio", "streamable-http", "sse"], default="stdio")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int)
    p.add_argument("--api", help="HTTP API 地址，默认取 runner.yaml")

    a = ap.parse_args(argv)
    cfg = load_runner_config(a.config)

    if a.cmd == "launch":
        return cmd_launch(cfg, a)
    if a.cmd == "chrome":
        from .chrome import launch_chrome
        print(launch_chrome(cfg, a.urls))
        return 0
    if a.cmd == "mcp":
        from .mcp_server import run_mcp
        api = a.api or f"http://{cfg.server.api_host}:{cfg.server.api_port}"
        from .config import app_root
        run_mcp(api, a.transport, a.host, a.port or cfg.server.mcp_port,
                db_path=cfg.resolve(cfg.output_dir) / "mcp_bridge.sqlite3", token_path=app_root() / "mcp.token")
        return 0

    if a.cmd in (None, "ui"):
        try:
            import tkinter  # noqa: F401  安装包自带的精简 Python 没有 tkinter，改用网页控制页
        except ImportError:
            print("当前 Python 没有桌面窗口组件（tkinter），改为打开网页控制页。")
            a.urls = getattr(a, "urls", None)
            return cmd_launch(cfg, a)
        from .ui.console import run_console
        run_console(Manager(cfg))
        return 0
    m = Manager(cfg)
    handlers = {"targets": cmd_targets, "run": cmd_run, "serve": cmd_serve, "calibrate": cmd_calibrate,
                "calibrate-desktop": cmd_calibrate_desktop, "dump-uia": cmd_dump_uia, "profiles": cmd_profiles}
    return handlers[a.cmd](m, a)


def _main_keep_window() -> int:
    """由一键启动打开的运行器窗口：出错时把报错写入 output/runner_error.log，并保留窗口等人看。"""
    import traceback
    try:
        rc = main()
    except KeyboardInterrupt:
        return 130
    except BaseException as e:  # noqa: BLE001
        if isinstance(e, SystemExit) and not e.code:
            return 0
        msg = traceback.format_exc()
        print(msg)
        rc = 1
        try:
            from .config import app_root
            log = app_root() / "output" / "runner_error.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            import time
            with log.open("a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
        except Exception:
            pass
    if rc and os.environ.get("ACR_KEEP_WINDOW_ON_ERROR"):
        try:
            input("\n运行器没有正常运行。请把上面的信息截图发给云知，然后按回车关闭本窗口。")
        except Exception:
            pass
    return rc


if __name__ == "__main__":
    sys.exit(_main_keep_window())
