"""会话记录器：每个会话一个文件夹，完整留档以便复盘。

output/<日期时间>_<画像>_<名称>/
├── session.json      会话元信息
├── rounds.jsonl      每轮一行：推进词、耗时、字数、命中规则、判定结论
├── transcript.md     完整对话
├── replies/NNN.md    每轮模型回答原文
├── shots/            暂停、报错、结束时的截图
└── events.log        底层事件
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


def _safe(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", name).strip(" ._")
    return name[:40] or "session"


class SessionRecorder:
    def __init__(self, output_root: Path, profile_id: str, name: str, meta: dict[str, Any]):
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        self.dir = Path(output_root) / f"{stamp}_{_safe(profile_id)}_{_safe(name)}"
        (self.dir / "replies").mkdir(parents=True, exist_ok=True)
        (self.dir / "shots").mkdir(exist_ok=True)
        self.meta = {"profile": profile_id, "name": name, "started_at": datetime.now().isoformat(timespec="seconds"),
                     **meta}
        self._write_meta()
        self._transcript = self.dir / "transcript.md"
        self._transcript.write_text(f"# {name}\n\n画像：{profile_id}　开始：{self.meta['started_at']}\n\n",
                                    encoding="utf-8")

    def _write_meta(self) -> None:
        (self.dir / "session.json").write_text(json.dumps(self.meta, ensure_ascii=False, indent=2), encoding="utf-8")

    def event(self, etype: str, message: str = "", **data: Any) -> None:
        line = {"t": datetime.now().isoformat(timespec="milliseconds"), "event": etype, "msg": message, **data}
        with (self.dir / "events.log").open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")

    def prompt(self, round_no: int, text: str, is_task: bool = False) -> None:
        title = "任务说明" if is_task else "推进词"
        with self._transcript.open("a", encoding="utf-8") as f:
            f.write(f"## 第 {round_no} 轮\n\n**{title}**：\n\n{text}\n\n")

    def reply(self, round_no: int, text: str, record: dict[str, Any]) -> None:
        (self.dir / "replies" / f"{round_no:03d}.md").write_text(text, encoding="utf-8")
        with self._transcript.open("a", encoding="utf-8") as f:
            f.write(f"**模型回答**（{len(text)} 字，判定：{record.get('action')} · {record.get('reason')}）：\n\n"
                    f"{text}\n\n---\n\n")
        with (self.dir / "rounds.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"round": round_no, "chars": len(text), **record}, ensure_ascii=False) + "\n")

    def shot(self, png: Optional[bytes], tag: str) -> Optional[Path]:
        if not png:
            return None
        p = self.dir / "shots" / f"{time.strftime('%H%M%S')}_{_safe(tag)}.png"
        p.write_bytes(png)
        return p

    def finish(self, **meta: Any) -> None:
        self.meta.update(meta)
        self.meta["ended_at"] = datetime.now().isoformat(timespec="seconds")
        self._write_meta()
