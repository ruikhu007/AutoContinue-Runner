"""判定器：读最后一条回答，按固定优先级决定 继续 / 结束 / 暂停 / 退避。

规则表（与技术文档第 7 节一致）：
 1 危险关键词、登录失效、验证码       → 暂停叫人
 2 【全部完成】                       → 结束
 3 【需要人类】                       → 暂停（或配置为自动采纳）
 4 限额 / 限流 / 繁忙                 → 退避后重试
 5 生成报错或回答为空                 → 继续（报错推进词）
 6 回答被截断                         → 继续（续写推进词）
 7 连续多轮高度重复                   → 暂停
 8 【步骤完成 n/N】                   → 继续（第 n+1 步）
   【本轮完成】（开放式推理）         → 继续（下一轮）
 9 无标记但以提问结尾                 → 继续（默认）或暂停
10 以上皆未命中                       → 继续（推进词池轮换）
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from ..config import Behaviour, Limits, Profile, ProtocolCfg

# 协议标记必须独占一行，且位于回答末尾几行内——避免模型复述协议时被误判。
_TAIL_LINES = 6
RE_DONE = re.compile(r"^\s*[*_`#>\s]*(?:【\s*全部完成\s*】|\[\s*ALL[ _-]?DONE\s*\])[*_`\s]*$", re.I)
RE_HUMAN = re.compile(r"^\s*[*_`#>\s]*(?:【\s*需要人类\s*】|\[\s*NEED[ _-]?HUMAN\s*\])", re.I)
RE_STEP = re.compile(
    r"^\s*[*_`#>\s]*(?:【\s*步骤完成\s*(\d+)\s*[/／]\s*(\d+)\s*】|\[\s*STEP\s*(\d+)\s*/\s*(\d+)\s*\])[*_`\s]*$", re.I
)
RE_ROUND = re.compile(r"^\s*[*_`#>\s]*(?:【\s*本轮完成\s*】|\[\s*ROUND[ _-]?DONE\s*\])[*_`\s]*$", re.I)
RE_ANY_MARK = re.compile(r"【\s*(?:全部完成|需要人类|本轮完成|步骤完成[^】]*)】|\[\s*(?:ALL[ _-]?DONE|NEED[ _-]?HUMAN|ROUND[ _-]?DONE|STEP[^\]]*)\]", re.I)
RE_QUESTION_HINT = re.compile(r"(请选择|您希望|你希望|是否需要|要不要|请确认|你想要|你更倾向|哪一个|哪种方案|Would you like|Do you want|Should I)")
_TRUNC_END = set("，、,;；：:（(《“‘-—")


@dataclass
class Decision:
    action: str                 # continue | done | pause | backoff
    rule: str                   # 命中规则编号与名称
    reason: str                 # 中文说明，写入日志与通知
    phrase_kind: str = "default"
    step: Optional[tuple[int, int]] = None
    had_marker: bool = False


@dataclass
class JudgeState:
    no_marker_rounds: int = 0
    last_step: Optional[tuple[int, int]] = None
    prev_reply: str = ""
    repeat_count: int = 0
    history: list[str] = field(default_factory=list)


def tail_lines(text: str, n: int = _TAIL_LINES) -> list[str]:
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    return lines[-n:]


def similarity(a: str, b: str, n: int = 3) -> float:
    """字符 n-gram Jaccard 相似度，0~1。"""
    a, b = re.sub(r"\s+", "", a), re.sub(r"\s+", "", b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ga = {a[i:i + n] for i in range(max(1, len(a) - n + 1))}
    gb = {b[i:i + n] for i in range(max(1, len(b) - n + 1))}
    return len(ga & gb) / max(1, len(ga | gb))


def _search_any(patterns: list[str], text: str) -> Optional[str]:
    for p in patterns:
        try:
            m = re.search(p, text, re.I)
        except re.error:
            m = re.search(re.escape(p), text, re.I)
        if m:
            return m.group(0)
    return None


class Judge:
    def __init__(self, profile: Profile, behaviour: Behaviour, limits: Limits, protocol: ProtocolCfg):
        self.profile = profile
        self.behaviour = behaviour
        self.limits = limits
        self.protocol = protocol
        self.state = JudgeState()

    # -------------------------------------------------------------- 主入口
    def decide(self, reply: str, alert: str = "", page_problem: str = "") -> Decision:
        d = self._decide(reply or "", alert or "", page_problem or "")
        self._update_state(reply or "", d)
        return d

    def _decide(self, reply: str, alert: str, page_problem: str) -> Decision:
        b = self.behaviour
        tail = tail_lines(reply)
        tail_text = "\n".join(tail)
        short_or_tail = reply if len(reply) < 400 else reply[-400:]

        # 1 危险 / 页面异常
        if page_problem:
            return Decision("pause", "1-page", f"页面异常：{page_problem}")
        if b.danger_check:
            hit = next((k for k in b.danger_keywords if k and k.lower() in reply.lower()), None)
            if hit:
                return Decision("pause", "1-danger", f"回答中出现危险操作关键词“{hit}”，需要人类确认")

        has_mark = bool(RE_ANY_MARK.search(tail_text))

        # 2 全部完成
        if any(RE_DONE.match(ln) for ln in tail):
            return Decision("done", "2-done", "模型报告【全部完成】", had_marker=True)

        # 3 需要人类
        if any(RE_HUMAN.match(ln) for ln in tail):
            if b.on_need_human == "auto":
                return Decision("continue", "3-human-auto", "模型请求人类决定，按配置自动采纳推荐方案",
                                phrase_kind="need_human_auto", had_marker=True)
            return Decision("pause", "3-human", "模型输出【需要人类】，等待人类决定", had_marker=True)

        # 4 限额 / 限流
        limit_hit = _search_any(self.profile.limit_patterns, alert) or (
            _search_any(self.profile.limit_patterns, short_or_tail) if len(reply) < 600 else None)
        if limit_hit:
            return Decision("backoff", "4-limit", f"检测到限额或限流提示“{limit_hit}”")

        # 5 报错 / 空回答
        if not reply.strip():
            return Decision("continue", "5-empty", "回答为空，视为生成中断", phrase_kind="error")
        err_hit = _search_any(self.profile.error_patterns, alert) or (
            _search_any(self.profile.error_patterns, reply) if len(reply) < 200 else None)
        if err_hit:
            return Decision("continue", "5-error", f"检测到生成报错“{err_hit}”", phrase_kind="error")

        # 6 截断
        if not has_mark and (reply.count("```") % 2 == 1 or reply.rstrip()[-1:] in _TRUNC_END):
            return Decision("continue", "6-truncated", "回答疑似被截断", phrase_kind="truncated")

        # 7 重复
        sim = similarity(reply, self.state.prev_reply) if self.state.prev_reply else 0.0
        if sim >= self.limits.repeat_threshold and self.state.repeat_count + 1 >= self.limits.repeat_rounds:
            return Decision("pause", "7-repeat",
                            f"连续 {self.limits.repeat_rounds} 轮回答高度重复（相似度 {sim:.2f}），模型可能在原地打转")

        # 8 步骤完成
        for ln in reversed(tail):
            m = RE_STEP.match(ln)
            if m:
                n = int(m.group(1) or m.group(3))
                total = int(m.group(2) or m.group(4))
                last = self.state.last_step
                if last and (n < last[0] or (total != last[1] and n <= last[0])):
                    return Decision("pause", "8-regress", f"步骤进度异常：从 {last[0]}/{last[1]} 变为 {n}/{total}",
                                    step=(n, total), had_marker=True)
                if n >= total:
                    # 最后一步完成但没写【全部完成】：再推一次让它收尾
                    return Decision("continue", "8-step-last", f"已完成第 {n}/{total} 步（最后一步）",
                                    phrase_kind="default", step=(n, total), had_marker=True)
                return Decision("continue", "8-step", f"已完成第 {n}/{total} 步", phrase_kind="step",
                                step=(n, total), had_marker=True)

        # 8' 本轮完成（开放式推理：没有总步数，模型每轮结束写【本轮完成】）
        if any(RE_ROUND.match(ln) for ln in tail):
            return Decision("continue", "8-round", "本轮完成（开放式），继续下一轮", phrase_kind="round", had_marker=True)

        # 9 提问
        last_two = "\n".join(tail[-2:])
        if last_two.rstrip().endswith(("？", "?")) or RE_QUESTION_HINT.search(last_two):
            if b.on_question == "pause":
                return Decision("pause", "9-question", "模型以提问结尾，按配置暂停")
            return Decision("continue", "9-question", "模型以提问结尾，按配置让其自行决定后继续", phrase_kind="question")

        # 10 默认
        return Decision("continue", "10-default", "未命中特殊规则，常规推进", had_marker=has_mark)

    def _update_state(self, reply: str, d: Decision) -> None:
        st = self.state
        if st.prev_reply and similarity(reply, st.prev_reply) >= self.limits.repeat_threshold:
            st.repeat_count += 1
        else:
            st.repeat_count = 0
        st.prev_reply = reply
        st.no_marker_rounds = 0 if d.had_marker else st.no_marker_rounds + 1
        if d.step:
            st.last_step = d.step

    def needs_reminder(self) -> bool:
        return self.protocol.enabled and self.state.no_marker_rounds >= self.protocol.remind_after
