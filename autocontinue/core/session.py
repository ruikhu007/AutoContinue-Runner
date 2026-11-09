"""单个续推会话：感知 → 判定 → 注入 → 核验 的状态机（技术文档第 2、6 节）。"""
from __future__ import annotations

import asyncio
import re
import time
from enum import Enum
from typing import Any, Optional

from ..adapters.base import Adapter, InjectError, InputBusyError
from ..config import DEFAULT_TEMPLATES, Profile, RunnerConfig, SessionSpec
from ..notify import Notifier
from ..recorder import SessionRecorder
from .guard import Guard
from .judge import Decision, Judge
from .phrases import PhrasePool
from .signals import Signals


class State(str, Enum):
    ATTACHING = "连接中"
    WAIT_START = "等待开始"
    GENERATING = "生成中"
    SETTLING = "稳定确认"
    JUDGE = "判定"
    INJECT = "注入"
    BACKOFF = "退避等待"
    PAUSED = "暂停叫人"
    DONE = "结束"
    STOPPED = "已停止"
    ERROR = "出错"


TERMINAL = {State.DONE, State.STOPPED, State.ERROR}


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


class Session:
    def __init__(self, sid: str, spec: SessionSpec, profile: Profile, cfg: RunnerConfig, adapter: Adapter,
                 recorder: SessionRecorder, notifier: Notifier):
        self.id = sid
        self.spec = spec
        self.profile = profile
        self.cfg = cfg
        self.adapter = adapter
        self.rec = recorder
        self.notifier = notifier
        self.name = spec.name or profile.name or profile.id

        behaviour = cfg.behaviour.model_copy()
        if spec.on_need_human:
            behaviour.on_need_human = spec.on_need_human
        self.protocol_on = cfg.protocol.enabled if spec.protocol is None else spec.protocol
        protocol = cfg.protocol.model_copy(update={"enabled": self.protocol_on})
        self.judge = Judge(profile, behaviour, cfg.limits, protocol)
        # 任务类型：steps 有限步骤（列出 N 步，【步骤完成 n/N】）；open 开放式（不知道总步数，每轮【本轮完成】）
        self.task_mode = spec.task_mode
        self.open_mode = spec.task_mode == "open"
        self.protocol_text = cfg.protocol.open_text if self.open_mode else cfg.protocol.text
        self.protocol_reminder = cfg.protocol.open_reminder if self.open_mode else cfg.protocol.reminder
        lim = cfg.limits
        self.guard = Guard(lim, spec.max_rounds or (lim.open_max_rounds if self.open_mode else None),
                           spec.max_runtime_h or (lim.open_max_runtime_h if self.open_mode else None))
        # 单轮最长时间：新建时可人工设定，运行中也可调整（set_limits）
        self.max_turn_min = float(spec.max_turn_min or profile.timing.max_turn_min)
        self._long_turn_noted = False
        # runner.yaml 里的 templates 会整体替换默认值；缺的种类（如新加的 round）用内置默认补上
        self.phrases = PhrasePool(cfg.phrases, {**DEFAULT_TEMPLATES, **(cfg.templates or {})})

        self.state = State.ATTACHING
        self.rounds = 0                     # 已注入的推进词数（不含首轮任务说明）
        self.turn = 0                       # 记录用的轮次编号
        self.progress: Optional[tuple[int, int]] = None
        self.last_decision: Optional[Decision] = None
        self.last_reason = ""
        self.target_desc = ""
        self.task: Optional[asyncio.Task] = None

        self._paused = False
        self._pause_kind = ""
        self._resume_evt = asyncio.Event()
        self._step_mode = False
        self._pause_fp: Optional[str] = None
        self._approved_fp: Optional[str] = None
        self._first_push_protocol = self.protocol_on and spec.mode == "takeover"
        self._last_prompt = ""
        self._reset_user_baseline = False
        self._user_baseline = ""             # 挂载 / 恢复时对话里最后一条用户消息
        self._own_heads: list[str] = []      # 我们发过的推进词开头（规范化后），用于区分人类发言

        # 每轮跟踪
        now = time.monotonic()
        self._base_fp: Optional[str] = None
        self._base_seq: Optional[int] = None     # 注入时的生成流计数
        self._base_msg: Optional[str] = None     # 注入时数据层里最新助手消息 id
        self._judged_msg: Optional[str] = None   # 上一次判定过的助手消息 id
        self._inject_at = now
        self._turn_start = now
        self._last_fp: Optional[str] = None
        self._last_change = now
        self._resubmits = 0

    # ================================================================ 对外控制
    async def pause(self, reason: str = "人类手动暂停", kind: str = "manual") -> None:
        await self._pause(reason, kind=kind, shot=False)

    async def resume(self) -> None:
        if not self._paused or self.state in TERMINAL:
            return
        if self._pause_kind == "limit":   # 因上限暂停后恢复：再放行 10 轮 / 重置计时
            self.guard.max_rounds = max(self.guard.max_rounds, self.rounds + 10)
            if self.guard.runtime_s() >= self.guard.max_runtime_h * 3600:
                self.guard.started = time.monotonic()
        self._approved_fp = self._pause_fp
        self._paused = False
        self._pause_kind = ""
        self._reset_user_baseline = True
        self.guard.reset_backoff()
        now = time.monotonic()
        self._turn_start = now
        self._last_change = now
        self.state = State.SETTLING
        self.rec.event("resume", "人类恢复")
        self.notifier.send("info", self.id, self.name, "已恢复自动推进")
        self._resume_evt.set()

    async def step(self) -> None:
        """单步推进：推一轮，判定后再停下。"""
        self._step_mode = True
        if self._paused:
            await self.resume()

    def set_limits(self, max_rounds: Optional[int] = None, max_turn_min: Optional[float] = None,
                   max_runtime_h: Optional[float] = None) -> dict:
        """运行中调整上限（人工设定）。"""
        changed = []
        if max_rounds:
            self.guard.max_rounds = int(max_rounds); changed.append(f"最大轮次 {int(max_rounds)}")
        if max_turn_min:
            self.max_turn_min = float(max_turn_min); changed.append(f"单轮最长 {float(max_turn_min):g} 分钟")
        if max_runtime_h:
            self.guard.max_runtime_h = float(max_runtime_h); changed.append(f"总运行时长 {float(max_runtime_h):g} 小时")
        if changed:
            self.rec.event("limits", "人工调整上限：" + "，".join(changed))
        return self.status()

    async def stop(self) -> None:
        if self.state in TERMINAL:
            return
        self.state = State.STOPPED
        self.guard.stop_clock()
        self.last_reason = "人类结束会话"
        self._paused = False
        self._resume_evt.set()
        self.rec.event("stop", self.last_reason)
        self.notifier.send("info", self.id, self.name, "会话已结束")

    def status(self) -> dict[str, Any]:
        d = self.last_decision
        return {
            "id": self.id, "name": self.name, "profile": self.profile.id, "target": self.target_desc,
            "state": self.state.value, "paused": self._paused, "rounds": self.rounds,
            "max_rounds": self.guard.max_rounds, "task_mode": self.task_mode,
            "max_turn_min": self.max_turn_min, "max_runtime_h": self.guard.max_runtime_h,
            "progress": f"{self.progress[0]}/{self.progress[1]}" if self.progress else "",
            "runtime_s": round(self.guard.runtime_s()), "ended": self.state in TERMINAL,
            "last_reason": self.last_reason or (d.reason if d else ""),
            "last_rule": d.rule if d else "", "output_dir": str(self.rec.dir),
        }

    # ================================================================ 主循环
    async def run(self) -> None:
        try:
            self.target_desc = await self.adapter.attach()
            self.rec.event("attach", self.target_desc)
            self.notifier.send("info", self.id, self.name, f"已挂载：{self.target_desc}")
            sig = await self.adapter.observe()
            self._user_baseline = _norm(sig.last_user_text)
            if self.spec.mode == "new" and self.spec.task:
                text = self.spec.task.strip()
                if self.protocol_on:
                    text += "\n\n" + self.protocol_text
                await self._do_inject(text, is_task=True)
            else:
                self.rec.prompt(0, "（接管已有对话）")
                self._last_fp = sig.fingerprint()
                self._last_change = time.monotonic()
                self._turn_start = time.monotonic()
                self._enter(State.GENERATING if not sig.generation_off() else State.SETTLING)

            while self.state not in TERMINAL:
                if self._paused:
                    await self._resume_evt.wait()
                    continue
                try:
                    await self._tick()
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001  页面临时异常不再直接结束会话：暂停，等人（或 AI）恢复
                    msg = f"{type(e).__name__}: {e}".splitlines()[0][:200]
                    self.rec.event("tick_error", f"{type(e).__name__}: {e}"[:1000])
                    try:
                        self.rec.shot(await self.adapter.snapshot(), "error")
                    except Exception:
                        pass
                    await self._pause(f"页面操作异常（{msg}），已暂停。检查对话页后点“恢复”即可继续", kind="page")
                    continue
                await asyncio.sleep(self.profile.timing.poll_ms / 1000)
        except asyncio.CancelledError:
            if self.state not in TERMINAL:
                self.state = State.STOPPED
                self.last_reason = "会话被取消"
        except Exception as e:
            self.state = State.ERROR
            self.last_reason = f"{type(e).__name__}: {e}"
            self.rec.event("error", self.last_reason)
            try:
                self.rec.shot(await self.adapter.snapshot(), "error")
            except Exception:
                pass
            self.notifier.send("error", self.id, self.name, f"运行出错：{self.last_reason}")
        finally:
            self.guard.stop_clock()
            st = self.status()
            self.rec.finish(state=self.state.value, rounds=self.rounds, reason=self.last_reason,
                            progress=st["progress"], runtime_s=st["runtime_s"], last_rule=st["last_rule"],
                            task_mode=self.task_mode, target=self.target_desc, session_id=self.id)
            try:
                await self.adapter.detach()
            except Exception:
                pass

    # ---------------------------------------------------------------- 内部
    async def _sent_despite_error(self, text: str) -> bool:
        """注入过程中出现非预期异常后，判断推进词是否其实已经提交：输入框里已没有它，且页面开始生成或出现了新内容。"""
        for _ in range(6):
            await asyncio.sleep(1)
            try:
                sig = await self.adapter.observe()
            except Exception:
                continue
            head = _norm(text)[:16]
            if head and head in _norm(sig.input_text or ""):
                return False
            if sig.generating or (sig.stream_seq is not None and self._base_seq is not None
                                  and sig.stream_seq > self._base_seq):
                return True
            if sig.last_user_text and head and head in _norm(sig.last_user_text):
                return True
        return False

    def _enter(self, new: State) -> None:
        if self.state in TERMINAL or (self._paused and new != State.PAUSED):
            return
        if new != self.state:
            self.rec.event("state", new.value, prev=self.state.value)
            self.state = new

    async def _pause(self, reason: str, kind: str = "auto", shot: bool = True) -> None:
        if self.state in TERMINAL or self._paused:
            return
        self._paused = True
        self._pause_kind = kind
        self._resume_evt.clear()
        self._pause_fp = self._last_fp
        self.state = State.PAUSED
        self.last_reason = reason
        self.rec.event("pause", reason, kind=kind)
        if shot:
            try:
                self.rec.shot(await self.adapter.snapshot(), "pause")
            except Exception:
                pass
        if self.state in TERMINAL:      # 截图期间会话已被结束
            return
        self.notifier.send("pause", self.id, self.name, reason, progress=self.status()["progress"],
                           rounds=self.rounds)

    async def _sleep(self, secs: float) -> bool:
        """可被暂停 / 结束打断的等待；被打断返回 False。"""
        end = time.monotonic() + secs
        while time.monotonic() < end:
            if self._paused or self.state in TERMINAL:
                return False
            await asyncio.sleep(min(0.5, end - time.monotonic()))
        return not (self._paused or self.state in TERMINAL)

    async def _tick(self) -> None:
        t = self.profile.timing
        sig = await self.adapter.observe()
        if self._paused or self.state in TERMINAL:
            return
        now = time.monotonic()

        if not sig.page_ok:
            await self._pause(f"页面异常：{sig.page_problem}", kind="page")
            return

        # 人类亲自发言检测：看“最后一条用户消息”的文字是不是我们自己发的。
        # 不用消息条数——ChatGPT 等站点会虚拟化长对话列表，旧消息被卸载 / 重新挂载，条数会来回跳。
        if self._reset_user_baseline:
            self._user_baseline = _norm(sig.last_user_text)
            self._reset_user_baseline = False
        if sig.last_user_text is not None and self._is_human_message(sig.last_user_text):
            self._user_baseline = _norm(sig.last_user_text)
            if self.cfg.behaviour.auto_pause_on_human_message:
                self.rec.event("human_message", sig.last_user_text[:200])
                await self._pause("检测到人类在对话中亲自发言，已切换为人类接管", kind="human", shot=False)
                return

        fp = sig.fingerprint()
        if fp != self._last_fp:
            self._last_fp = fp
            self._last_change = now

        if self.state == State.WAIT_START:
            await self._check_started(sig, fp, now)
            return

        if self.state in (State.GENERATING, State.SETTLING):
            limit = self.max_turn_min * 60
            turn_len = now - self._turn_start
            if turn_len > limit:
                quiet = now - self._last_change            # 多久没有任何新输出
                streaming = bool(sig.net_active)
                still_working = (streaming and turn_len < 3 * limit) or quiet < t.stall_min * 60
                if still_working:
                    if not self._long_turn_noted:
                        self._long_turn_noted = True
                        self.rec.event("long_turn", f"本轮已超过 {self.max_turn_min:g} 分钟，但模型仍在输出"
                                                    f"（{'生成流未断' if streaming else f'最近 {t.stall_min:g} 分钟内有新内容'}），继续等待")
                else:
                    await self._pause(f"本轮已超过 {self.max_turn_min:g} 分钟，且 {quiet / 60:.0f} 分钟没有任何新输出，"
                                      "疑似卡死。可在控制页调大“单轮最长时间”后点“恢复”", kind="timeout")
                    return
            off = sig.generation_off() and sig.done_marker is not False
            settle_ms = t.settle_ms
            if sig.authoritative:
                # 以网络流 + 数据层为准：不等页面文字稳定，只做短暂确认；且必须是一条“新的”回答
                settle_ms = min(t.settle_ms, t.auth_settle_ms)
                if off and sig.msg_id is not None and sig.msg_id == self._judged_msg and not self._approved_fp:
                    off = False
                data = sig.extra.get("data") or {}
                idle = sig.extra.get("stream_idle_s")
                if (not sig.net_active and not sig.turn_done and data.get("curRole") == "user"
                        and idle is not None and idle > t.start_timeout_s):
                    await self._pause("生成流已结束，但会话里没有出现新的回答（可能生成失败或被中断）", kind="stream")
                    return
            stable_ms = (now - self._last_change) * 1000
            if off and stable_ms >= settle_ms:
                self._enter(State.JUDGE)
            else:
                self._enter(State.SETTLING if off else State.GENERATING)
                return

        if self.state == State.JUDGE:
            await self._judge(sig)

    def _is_human_message(self, text: str) -> bool:
        """最后一条用户消息既不是注入前就有的那条，也不是我们发过的推进词 → 人类发的。"""
        cur = _norm(text)
        if not cur or cur == self._user_baseline:
            return False
        for own in self._own_heads:
            if cur.startswith(own) or own.startswith(cur[:len(own)]):
                return False
        return True

    async def _check_started(self, sig: Signals, fp: str, now: float) -> None:
        t = self.profile.timing
        if sig.authoritative or sig.stream_seq is not None:
            # 有网络流：以“注入后发起了新的生成请求”为准，不看页面渲染
            started = (
                (sig.stream_seq or 0) > (self._base_seq or 0)
                or bool(sig.net_active)
                or (sig.authoritative and sig.msg_id is not None and sig.msg_id != self._base_msg)
            )
        else:
            # 不用回答条数判断：虚拟化列表里旧回答重新挂载也会让条数变大
            started = (
                sig.generating is True
                or (fp != self._base_fp and bool(sig.reply_text or sig.reply_hash))
            )
        if started:
            self._turn_start = now
            self._last_change = now
            self._resubmits = 0
            self._enter(State.GENERATING)
            return
        if now - self._inject_at < t.start_timeout_s:
            return
        # 超时未开始：输入框里还留着推进词 → 提交没成功，只重新提交
        head = _norm(self._last_prompt)[:12]
        if sig.input_text and head and head in _norm(sig.input_text) and self._resubmits < t.inject_retry:
            self._resubmits += 1
            did = await self.adapter.resubmit()
            self.rec.event("resubmit", f"第 {self._resubmits} 次重新提交", done=did)
            self._inject_at = now
            return
        await self._pause(f"注入后 {t.start_timeout_s:.0f} 秒内未检测到模型开始生成", kind="inject")

    async def _judge(self, sig: Signals) -> None:
        try:
            reply = await self.adapter.read_last_reply()
        except Exception as e:
            self.rec.event("read_error", str(e))
            reply = sig.reply_text
        elapsed = round(time.monotonic() - self._turn_start, 1)

        fp = sig.fingerprint()
        if self._approved_fp and fp == self._approved_fp:
            # 沿用已有进度，推进词仍然写“第 n+1 步”，而不是笼统的“下一步”
            d = Decision("continue", "0-approved", "人类恢复且回答未变，视为批准，继续推进",
                         phrase_kind="step" if self.progress else "default", step=self.progress)
            self.judge.state.prev_reply = reply
        else:
            d = self.judge.decide(reply, sig.alert)
        self._approved_fp = None
        if sig.authoritative:
            self._judged_msg = sig.msg_id
        self.last_decision = d
        self.last_reason = d.reason
        if d.step:
            self.progress = d.step
        self.rec.reply(self.turn, reply, {"action": d.action, "rule": d.rule, "reason": d.reason,
                                          "step": list(d.step) if d.step else None, "elapsed_s": elapsed})

        if d.action == "done":
            self.state = State.DONE
            self.rec.event("done", d.reason)
            try:
                self.rec.shot(await self.adapter.snapshot(), "done")
            except Exception:
                pass
            self.notifier.send("done", self.id, self.name, f"任务完成，共推进 {self.rounds} 轮",
                               output=str(self.rec.dir))
            return
        if d.action == "pause":
            await self._pause(d.reason, kind="judge")
            return
        if self._step_mode:
            self._step_mode = False
            await self._pause("单步推进完成，等待人类", kind="step", shot=False)
            return

        kind = d.phrase_kind
        if d.action == "backoff":
            secs = self.guard.next_backoff()
            if secs is None:
                await self._pause(f"{d.reason}；多次退避后仍受限", kind="limit")
                return
            self._enter(State.BACKOFF)
            self.notifier.send("info", self.id, self.name, f"{d.reason}，{secs:.0f} 秒后重试")
            if not await self._sleep(secs):
                return
            kind = "error"
        else:
            self.guard.reset_backoff()

        reason = self.guard.check_before_inject(self.rounds)
        if reason:
            await self._pause(reason, kind="limit")
            return

        step = d.step
        if self.open_mode and kind in ("default", "step"):
            kind = "round"
        phrase = self.phrases.pick(kind, next=step[0] + 1 if step else None, total=step[1] if step else None,
                                   task_name=self.name)
        if self._first_push_protocol:
            phrase += "\n\n" + self.protocol_text
            self._first_push_protocol = False
        elif self.judge.needs_reminder():
            phrase += "\n" + self.protocol_reminder
            self.judge.state.no_marker_rounds = 0
        await self._do_inject(phrase)

    async def _settle_foreign_input(self, first: str) -> str:
        """返回 go（可以注入）或 stop（已暂停 / 已停止）。"""
        t = self.profile.timing
        self.rec.event("input_busy", f"输入框里有不是运行器写的内容：{first[:60]!r}，先观察是否有人在打字")
        start = last_change = time.monotonic()
        prev = first
        while True:
            if not await self._sleep(5):
                return "stop"
            cur = await self.adapter.foreign_input()
            now = time.monotonic()
            if not cur:
                # 内容没了：可能是人发出了消息，也可能是人自己删掉了。人发了消息就交给人类
                sig = await self.adapter.observe()
                if sig.generating or self._is_human_message(sig.last_user_text or ""):
                    await self._pause("人类在对话里发了消息，已暂停接管", kind="human")
                    return "stop"
                self.rec.event("input_free", "输入框已清空，继续推进")
                return "go"
            if cur != prev:
                prev, last_change = cur, now
            idle = now - last_change
            if idle >= t.input_stale_s:
                if len(cur) <= t.stray_max_chars:
                    if await self.adapter.clear_input():
                        self.rec.event("input_cleared",
                                       f"输入框里的 {cur!r} 已 {idle:.0f} 秒没有变化，判定为误触的残留字符，已清除后继续推进")
                        return "go"
                await self._pause(f"对话输入框里有 {idle:.0f} 秒未变化、尚未发送的内容（{cur[:30]!r}），"
                                  "为避免覆盖人类草稿已暂停。请发送或清空后点“恢复”", kind="input")
                return "stop"
            if now - start >= t.input_wait_max_s:
                await self._pause("对话输入框里一直有人在输入，已等待 "
                                  f"{t.input_wait_max_s / 60:.0f} 分钟，暂停叫人", kind="input")
                return "stop"

    async def _do_inject(self, text: str, is_task: bool = False) -> None:
        t = self.profile.timing
        self._enter(State.INJECT)
        wait = self.guard.wait_needed()
        if wait and not await self._sleep(wait):
            return
        while True:
            busy = await self.adapter.ready_to_inject()
            if not busy:
                break
            self.rec.event("defer", busy)
            if not await self._sleep(2):
                return
        if self._paused or self.state in TERMINAL:
            return

        # 输入框里有不是运行器写的内容：先观察，分清“人在打字 / 人写好的草稿 / 误触的残留字符”
        foreign = await self.adapter.foreign_input()
        if foreign:
            verdict = await self._settle_foreign_input(foreign)
            if verdict != "go":
                return

        sig = await self.adapter.observe()
        self._base_fp = sig.fingerprint()
        self._base_seq = sig.stream_seq
        self._base_msg = sig.msg_id

        if not is_task:
            self.turn += 1
        self.rec.prompt(self.turn, text, is_task=is_task)

        last_err: Optional[Exception] = None
        for attempt in range(1, t.inject_retry + 1):
            try:
                await self.adapter.inject(text)
                last_err = None
                break
            except InputBusyError as e:
                self.rec.event("inject_fail", str(e), attempt=attempt)
                await self._pause(str(e), kind="input")
                return
            except InjectError as e:
                last_err = e
                self.rec.event("inject_fail", str(e), attempt=attempt)
                if not await self._sleep(2):
                    return
            except Exception as e:  # noqa: BLE001  非预期的页面异常：先看消息是否其实已经发出，避免重复发送
                last_err = e
                self.rec.event("inject_fail", f"{type(e).__name__}: {e}"[:300], attempt=attempt)
                if await self._sent_despite_error(text):
                    self.rec.event("inject_recovered", "提交时页面报错，但推进词已发出，继续")
                    last_err = None
                    break
                if not await self._sleep(2):
                    return
        if last_err:
            await self._pause(f"注入失败：{last_err}", kind="inject")
            return

        self.guard.mark_inject()
        if not is_task:
            self.rounds += 1
        self._last_prompt = text
        self._own_heads = (self._own_heads + [_norm(text)[:16]])[-20:]
        now = time.monotonic()
        self._inject_at = now
        self._turn_start = now
        self._long_turn_noted = False
        self._resubmits = 0
        self._enter(State.WAIT_START)
        first_line = text.strip().splitlines()[0][:40]
        label = "已发送任务说明" if is_task else f"第 {self.rounds} 轮推进"
        self.rec.event("inject", text, round=self.rounds)
        self.notifier.send("info", self.id, self.name, f"{label}：{first_line}")
