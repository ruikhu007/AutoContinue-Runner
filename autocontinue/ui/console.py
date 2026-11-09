"""控制台小窗口（tkinter，Windows 版 Python 自带，无需额外安装）。

一行一个会话：状态、进度、轮次、时长、最近判定；暂停 / 恢复 / 单步 / 结束；新建会话与网页校准。
后台线程跑 asyncio 事件循环（会话、HTTP API），界面线程只做展示与转发。
"""
from __future__ import annotations

import asyncio
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Optional

from ..config import SessionSpec
from ..hotkeys import start_hotkeys
from ..manager import Manager

COLS = [("name", "名称", 150), ("profile", "画像", 110), ("state", "状态", 80), ("progress", "进度", 60),
        ("rounds", "轮次", 60), ("runtime", "时长", 70), ("reason", "最近判定 / 暂停原因", 360)]


def _fmt_secs(s: int) -> str:
    h, rem = divmod(int(s), 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


class Console:
    def __init__(self, manager: Manager, with_api: bool = True):
        self.m = manager
        self.events: "queue.Queue[dict]" = queue.Queue()
        self.ui_calls: "queue.Queue[tuple]" = queue.Queue()
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True, name="acr-loop").start()
        self.m.notifier.subscribe(self.events.put)
        if with_api:
            self._start_api()
        if self.m.cfg.hotkeys:
            start_hotkeys(lambda: self.submit(self.m.pause_all()), lambda: self.submit(self.m.resume_all()),
                          log=lambda s: self.events.put({"level": "info", "name": "热键", "message": s}))

        self.root = tk.Tk()
        self.root.title("续推运行器 AutoContinue Runner")
        self.root.geometry("1040x560")
        self.root.minsize(760, 400)
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(500, self._refresh)
        self.root.after(200, self._drain_events)

    # ---------------------------------------------------------------- 异步桥
    def submit(self, coro, done=None):
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        if done:
            def cb(f):
                try:
                    res, err = f.result(), None
                except Exception as e:
                    res, err = None, e
                self.ui_calls.put((done, res, err))   # 交回界面线程执行
            fut.add_done_callback(cb)
        return fut

    def _start_api(self) -> None:
        try:
            from ..api import serve_api
        except ImportError:
            self.events.put({"level": "info", "name": "API", "message": "未安装 fastapi，HTTP API 未启动"})
            return
        srv = self.m.cfg.server

        async def run():
            try:
                await serve_api(self.m, srv.api_host, srv.api_port)
            except BaseException as e:  # 端口被占用时 uvicorn 会 SystemExit
                self.events.put({"level": "error", "name": "API", "message": f"HTTP API 启动失败：{e}"})

        self.submit(run())
        self.events.put({"level": "info", "name": "API", "message": f"HTTP API：http://{srv.api_host}:{srv.api_port}"})

    # ---------------------------------------------------------------- 界面
    def _build(self) -> None:
        top = ttk.Frame(self.root, padding=(8, 8, 8, 0))
        top.pack(fill="x")
        for text, cmd in (("新建会话", self._new_session), ("全部暂停", lambda: self.submit(self.m.pause_all())),
                          ("全部恢复", lambda: self.submit(self.m.resume_all())), ("打开浏览器", self._open_chrome),
                          ("校准网页", self._calibrate), ("打开输出目录", self._open_output)):
            ttk.Button(top, text=text, command=cmd).pack(side="left", padx=(0, 6))
        ttk.Label(top, text="热键：Ctrl+Alt+P 全部暂停　Ctrl+Alt+R 全部恢复", foreground="#666").pack(side="right")

        mid = ttk.Frame(self.root, padding=8)
        mid.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(mid, columns=[c[0] for c in COLS], show="headings", height=8, selectmode="browse")
        for key, label, w in COLS:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=w, anchor="w", stretch=key == "reason")
        self.tree.tag_configure("paused", background="#fff4e5")
        self.tree.tag_configure("done", background="#ebfbee")
        self.tree.tag_configure("error", background="#fff5f5")
        self.tree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        sb.pack(side="left", fill="y")
        self.tree.configure(yscrollcommand=sb.set)

        ops = ttk.Frame(self.root, padding=(8, 0))
        ops.pack(fill="x")
        for text, action in (("暂停", "pause"), ("恢复", "resume"), ("单步推进", "step"), ("结束", "stop")):
            ttk.Button(ops, text=text, command=lambda a=action: self._act(a)).pack(side="left", padx=(0, 6))
        ttk.Button(ops, text="打开记录", command=self._open_record).pack(side="left", padx=(0, 6))

        logf = ttk.LabelFrame(self.root, text="事件", padding=6)
        logf.pack(fill="both", expand=False, padx=8, pady=8)
        self.log = tk.Text(logf, height=9, wrap="word", state="disabled", font=("Microsoft YaHei UI", 9))
        self.log.pack(fill="both", expand=True)
        self.log.tag_configure("pause", foreground="#d9480f")
        self.log.tag_configure("error", foreground="#c92a2a")
        self.log.tag_configure("done", foreground="#2b8a3e")

    def _selected(self) -> Optional[str]:
        sel = self.tree.selection()
        return sel[0] if sel else None

    def _act(self, action: str) -> None:
        sid = self._selected()
        if not sid:
            messagebox.showinfo("续推运行器", "请先在列表中选中一个会话")
            return
        self.submit(getattr(self.m, action)(sid))

    def _refresh(self) -> None:
        async def snap():
            return self.m.statuses()

        def apply(rows, err):
            if rows is not None:
                seen = set()
                for st in rows:
                    sid = st["id"]
                    seen.add(sid)
                    vals = (st["name"], st["profile"], st["state"], st["progress"],
                            f"{st['rounds']}/{st['max_rounds']}", _fmt_secs(st["runtime_s"]), st["last_reason"])
                    tag = ("paused",) if st["paused"] else ("done",) if st["state"] == "结束" else \
                        ("error",) if st["state"] == "出错" else ()
                    if self.tree.exists(sid):
                        self.tree.item(sid, values=vals, tags=tag)
                    else:
                        self.tree.insert("", "end", iid=sid, values=vals, tags=tag)
                for iid in self.tree.get_children():
                    if iid not in seen:
                        self.tree.delete(iid)
            self.root.after(1000, self._refresh)

        self.submit(snap(), apply)

    def _drain_events(self) -> None:
        try:
            while True:
                ev = self.events.get_nowait()
                self._log(ev)
        except queue.Empty:
            pass
        try:
            while True:
                fn, res, err = self.ui_calls.get_nowait()
                try:
                    fn(res, err)
                except Exception as e:
                    self._log({"level": "error", "name": "界面", "message": str(e)})
        except queue.Empty:
            pass
        self.root.after(100, self._drain_events)

    def _log(self, ev: dict[str, Any]) -> None:
        level = ev.get("level", "info")
        line = f"{ev.get('time', '')[11:19]} {ev.get('name', '')}：{ev.get('message', '')}\n"
        self.log.configure(state="normal")
        self.log.insert("end", line, (level,))
        self.log.see("end")
        self.log.configure(state="disabled")
        if level in ("pause", "error", "done"):
            self._alert()

    def _alert(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(1500, lambda: self.root.attributes("-topmost", False))

    # ---------------------------------------------------------------- 对话框
    def _load_targets(self, done) -> None:
        async def go():
            self.m.reload_profiles()
            return await self.m.list_targets()
        self.submit(go(), done)

    def _new_session(self) -> None:
        dlg = tk.Toplevel(self.root)
        dlg.title("新建续推会话")
        dlg.geometry("640x560")
        dlg.transient(self.root)
        f = ttk.Frame(dlg, padding=12)
        f.pack(fill="both", expand=True)

        ttk.Label(f, text="目标（标签页 / 窗口）").grid(row=0, column=0, sticky="w")
        target_var = tk.StringVar()
        target_cb = ttk.Combobox(f, textvariable=target_var, state="readonly", width=70)
        target_cb.grid(row=1, column=0, columnspan=3, sticky="we", pady=(0, 8))

        ttk.Label(f, text="站点画像").grid(row=2, column=0, sticky="w")
        prof_var = tk.StringVar()
        prof_cb = ttk.Combobox(f, textvariable=prof_var, state="readonly", width=30,
                               values=[f"{p.id} · {p.name}" for p in self.m.profiles.values()])
        prof_cb.grid(row=3, column=0, sticky="w", pady=(0, 8))

        ttk.Label(f, text="会话名称").grid(row=2, column=1, sticky="w")
        name_var = tk.StringVar()
        ttk.Entry(f, textvariable=name_var, width=28).grid(row=3, column=1, sticky="w", pady=(0, 8))

        mode_var = tk.StringVar(value="new")
        mf = ttk.Frame(f)
        mf.grid(row=4, column=0, columnspan=3, sticky="w")
        ttk.Radiobutton(mf, text="新任务：发送下方任务说明（自动附加推进协议）", variable=mode_var, value="new").pack(anchor="w")
        ttk.Radiobutton(mf, text="接管已有对话：从当前回答开始推进", variable=mode_var, value="takeover").pack(anchor="w")

        ttk.Label(f, text="任务说明").grid(row=5, column=0, sticky="w", pady=(8, 0))
        task_txt = tk.Text(f, height=10, wrap="word")
        task_txt.grid(row=6, column=0, columnspan=3, sticky="nsew")
        f.rowconfigure(6, weight=1)
        f.columnconfigure(2, weight=1)

        of = ttk.Frame(f)
        of.grid(row=7, column=0, columnspan=3, sticky="w", pady=8)
        ttk.Label(of, text="最大轮次").pack(side="left")
        rounds_var = tk.IntVar(value=self.m.cfg.limits.max_rounds)
        ttk.Spinbox(of, from_=1, to=500, textvariable=rounds_var, width=6).pack(side="left", padx=(4, 16))
        ttk.Label(of, text="模型输出【需要人类】时").pack(side="left")
        human_var = tk.StringVar(value=self.m.cfg.behaviour.on_need_human)
        ttk.Radiobutton(of, text="暂停叫人", variable=human_var, value="pause").pack(side="left", padx=4)
        ttk.Radiobutton(of, text="自动采纳推荐方案", variable=human_var, value="auto").pack(side="left")

        targets: list[dict] = []

        def fill(res, err):
            if err:
                messagebox.showerror("续推运行器", str(err), parent=dlg)
                return
            targets.clear()
            for t in res or []:
                if "error" in t:
                    self._log({"level": "error", "name": "目标", "message": t["error"]})
                    continue
                targets.append(t)
            target_cb["values"] = [f"[{t['kind']}] {t['title'][:40]} — {t['url'][:60]}  ({t['short']})" for t in targets]
            if targets:
                target_cb.current(0)
                on_target()

        def on_target(*_):
            i = target_cb.current()
            if 0 <= i < len(targets) and targets[i]["profiles"]:
                pid = targets[i]["profiles"][0]
                for j, v in enumerate(prof_cb["values"]):
                    if v.startswith(pid + " "):
                        prof_cb.current(j)

        target_cb.bind("<<ComboboxSelected>>", on_target)
        self._load_targets(fill)

        def start():
            i = target_cb.current()
            if not prof_var.get():
                messagebox.showwarning("续推运行器", "请选择站点画像", parent=dlg)
                return
            task = task_txt.get("1.0", "end").strip()
            if mode_var.get() == "new" and not task:
                messagebox.showwarning("续推运行器", "新任务模式需要填写任务说明", parent=dlg)
                return
            spec = SessionSpec(profile_id=prof_var.get().split(" · ")[0],
                               target=targets[i]["target"] if 0 <= i < len(targets) else None,
                               task=task or None, mode=mode_var.get(), name=name_var.get() or None,
                               max_rounds=rounds_var.get(), on_need_human=human_var.get())

            def started(res, err):
                if err:
                    messagebox.showerror("启动失败", str(err), parent=dlg)
                else:
                    dlg.destroy()

            self.submit(self.m.start(spec), started)

        bf = ttk.Frame(f)
        bf.grid(row=8, column=0, columnspan=3, sticky="e")
        ttk.Button(bf, text="刷新目标", command=lambda: self._load_targets(fill)).pack(side="left", padx=6)
        ttk.Button(bf, text="挂载并启动", command=start).pack(side="left")

    def _calibrate(self) -> None:
        dlg = tk.Toplevel(self.root)
        dlg.title("校准网页站点")
        dlg.transient(self.root)
        f = ttk.Frame(dlg, padding=12)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="在浏览器里打开要校准的大模型页面（新对话即可），然后选择它：").pack(anchor="w")
        cb = ttk.Combobox(f, state="readonly", width=80)
        cb.pack(fill="x", pady=6)
        row = ttk.Frame(f)
        row.pack(fill="x")
        ttk.Label(row, text="画像 ID（英文）").pack(side="left")
        id_var = tk.StringVar()
        ttk.Entry(row, textvariable=id_var, width=18).pack(side="left", padx=(4, 12))
        ttk.Label(row, text="名称").pack(side="left")
        name_var = tk.StringVar()
        ttk.Entry(row, textvariable=name_var, width=18).pack(side="left", padx=4)
        ttk.Label(f, text="开始后请切到浏览器，按页面顶部提示依次点选；过程中会发送一条测试消息。",
                  foreground="#666").pack(anchor="w", pady=6)
        targets: list[dict] = []

        def fill(res, err):
            targets[:] = [t for t in (res or []) if t.get("kind") == "web" and "error" not in t]
            cb["values"] = [f"{t['title'][:40]} — {t['url'][:70]}" for t in targets]
            if targets:
                cb.current(0)

        self._load_targets(fill)

        def go():
            i = cb.current()
            pid = id_var.get().strip()
            if i < 0 or not pid:
                messagebox.showwarning("续推运行器", "请选择页面并填写画像 ID", parent=dlg)
                return
            from ..calibrate import calibrate_web
            log = lambda s: self.events.put({"level": "info", "name": "校准", "message": s})  # noqa: E731

            def finished(res, err):
                if err:
                    messagebox.showerror("校准失败", str(err), parent=self.root)
                else:
                    self.m.reload_profiles()
                    messagebox.showinfo("校准完成", f"画像 {res.id} 已保存。建议先观察几轮再长时间无人值守。",
                                        parent=self.root)

            self.submit(calibrate_web(self.m.cfg, self.m.hub(), targets[i]["target"], pid, name_var.get().strip(),
                                      log=log), finished)
            dlg.destroy()

        ttk.Button(f, text="开始校准", command=go).pack(anchor="e")

    def _open_chrome(self) -> None:
        from ..chrome import launch_chrome
        try:
            self._log({"level": "info", "name": "浏览器", "message": launch_chrome(self.m.cfg)})
        except Exception as e:
            messagebox.showerror("续推运行器", str(e))

    def _open_path(self, path: str) -> None:
        os.makedirs(path, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open" if sys.platform.startswith("linux") else "open", path])

    def _open_output(self) -> None:
        self._open_path(str(self.m.cfg.resolve(self.m.cfg.output_dir)))

    def _open_record(self) -> None:
        sid = self._selected()
        if sid and sid in self.m.sessions:
            self._open_path(str(self.m.sessions[sid].rec.dir))

    def _on_close(self) -> None:
        active = [s for s in self.m.sessions.values() if s.state.value not in ("结束", "已停止", "出错")]
        if active and not messagebox.askyesno("续推运行器", f"还有 {len(active)} 个会话在运行，确定全部结束并退出？"):
            return
        try:
            self.submit(self.m.shutdown()).result(timeout=8)
        except Exception:
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def run_console(manager: Manager, with_api: bool = True) -> None:
    Console(manager, with_api).run()
