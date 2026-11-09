"""适配器抽象接口。调度核心只通过这几个方法操作目标，对网页与桌面一视同仁。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from ..core.signals import Signals


class InjectError(Exception):
    pass


class InputBusyError(InjectError):
    """输入框里有不是运行器写入的内容（可能是人类正在输入），不能覆盖。"""


class Adapter(ABC):
    #: 是否会抢占键盘焦点（视觉档）；为 True 时调度核心会先等待用户空闲
    steals_focus: bool = False
    #: attach 成功后的目标描述；不为 None 表示已连接
    attached_desc: Optional[str] = None
    #: 目标的唯一标识（标签页 / 窗口），用于防止同一目标上同时跑两个会话
    target_key: Optional[str] = None

    @abstractmethod
    async def attach(self) -> str:
        """连接目标，返回人类可读的目标描述。重复调用应直接返回已连接的描述。"""

    @abstractmethod
    async def observe(self) -> Signals:
        """采样一次感知信号。"""

    @abstractmethod
    async def read_last_reply(self) -> str:
        """读取最后一条模型回答的完整文本（判定前调用一次）。"""

    @abstractmethod
    async def inject(self, text: str) -> None:
        """把文本写入输入框并提交；失败抛 InjectError。"""

    async def resubmit(self) -> bool:
        """输入框里仍有文本时，仅重新点一次提交。返回是否执行。"""
        return False

    async def snapshot(self) -> Optional[bytes]:
        return None

    async def ready_to_inject(self) -> Optional[str]:
        """返回非空字符串表示现在不宜注入（例如用户正在操作电脑），调度核心会稍后再试。"""
        return None

    async def foreign_input(self) -> str:
        """输入框里不是运行器写入的内容（没有则返回空串）。不支持的适配器返回空串。"""
        return ""

    async def clear_input(self) -> bool:
        """清空输入框。返回是否清空成功。"""
        return False

    async def detach(self) -> None:
        return None


class PageUnresponsiveError(RuntimeError):
    """标签页存在，但页面脚本对自动化没有响应。"""
