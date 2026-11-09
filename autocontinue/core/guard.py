"""熔断守卫：轮次、时长、注入间隔等硬上限。"""
from __future__ import annotations

import time
from typing import Optional

from ..config import Limits


class Guard:
    def __init__(self, limits: Limits, max_rounds: Optional[int] = None, max_runtime_h: Optional[float] = None):
        self.limits = limits
        self.max_rounds = max_rounds or limits.max_rounds
        self.max_runtime_h = max_runtime_h or limits.max_runtime_h
        self.started = time.monotonic()
        self.ended: Optional[float] = None
        self.last_inject: Optional[float] = None
        self.backoff_index = 0

    def runtime_s(self) -> float:
        return (self.ended or time.monotonic()) - self.started

    def stop_clock(self) -> None:
        """会话结束：计时停在这一刻，不再增长。"""
        if self.ended is None:
            self.ended = time.monotonic()

    def check_before_inject(self, rounds_done: int) -> Optional[str]:
        """返回非空字符串表示必须暂停的原因。"""
        if rounds_done >= self.max_rounds:
            return f"已达到最大推进轮次 {self.max_rounds}"
        if self.runtime_s() >= self.max_runtime_h * 3600:
            return f"已达到最长运行时间 {self.max_runtime_h:g} 小时"
        return None

    def wait_needed(self) -> float:
        if self.last_inject is None:
            return 0.0
        return max(0.0, self.limits.min_interval_s - (time.monotonic() - self.last_inject))

    def mark_inject(self) -> None:
        self.last_inject = time.monotonic()

    def next_backoff(self) -> Optional[float]:
        """返回下一次退避秒数；退避次数用尽返回 None。"""
        if self.backoff_index >= len(self.limits.backoff_s):
            return None
        s = self.limits.backoff_s[self.backoff_index]
        self.backoff_index += 1
        return s

    def reset_backoff(self) -> None:
        self.backoff_index = 0
