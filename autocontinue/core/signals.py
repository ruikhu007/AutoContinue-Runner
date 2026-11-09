"""适配器每次采样返回的感知信号。None 表示该信号在此目标上不可用。"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Signals:
    generating: Optional[bool] = None    # 是否正在生成（有网络流 / 数据层时以它们为准，否则看停止按钮）
    done_marker: Optional[bool] = None   # 回答结束后才出现的操作条是否已出现
    net_active: Optional[int] = None     # 在途的生成流请求数（网络层）
    stream_seq: Optional[int] = None     # 累计发起过的生成流请求数，用来判断“注入后是否真的开始生成”
    authoritative: bool = False          # True = 生成状态与回答原文来自网络流 + 数据层，而不是页面渲染
    turn_done: Optional[bool] = None     # 数据层：最新一条是否为已完成的助手消息
    msg_id: Optional[str] = None         # 数据层：最新助手消息 id，用来确认“这是一条新回答”
    reply_text: str = ""                 # 最后一条模型回答（有数据层时为完整原文）
    reply_hash: str = ""                 # 回答指纹，用于稳定判断
    reply_count: Optional[int] = None    # 模型回答条数
    user_count: Optional[int] = None     # 用户消息条数（仅供参考，虚拟化列表下不可靠）
    last_user_text: Optional[str] = None # 最后一条用户消息文字，用于识别人类亲自发言
    input_text: Optional[str] = None     # 输入框当前内容
    alert: str = ""                      # 页面上的报错 / 提示条文本
    page_ok: bool = True
    page_problem: str = ""
    extra: dict = field(default_factory=dict)

    def fingerprint(self) -> str:
        if self.reply_hash:
            return self.reply_hash
        return hashlib.md5(self.reply_text.encode("utf-8", "ignore")).hexdigest()

    def generation_off(self) -> bool:
        """生成信号是否已消失。所有信号都不可用时视为“已消失”，只靠文本稳定判断。"""
        if self.generating is True:
            return False
        if self.net_active:
            return False
        return True
