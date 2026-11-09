"""配置模型：runner.yaml（运行器全局配置）与 profiles/*.yaml（站点画像）。"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field


def app_root() -> Path:
    """程序根目录：源码运行时为项目根；PyInstaller 打包后为 exe 所在目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- 默认文本

DEFAULT_PROTOCOL = """【协作协议】本任务将分步自动推进，我不会逐步审阅。
1. 请先列出全部步骤（共 N 步），然后每次只完成一步。
2. 每步结束时，最后一行单独写：【步骤完成 n/N】
3. 全部完成时，最后一行单独写：【全部完成】
4. 遇到必须由我决定的问题，最后一行写：【需要人类】并说明原因。
5. 可以自行决定的细节，请按你的最佳判断直接做，不要问我。"""

DEFAULT_REMINDER = "（请记得：每步结束时最后一行单独写【步骤完成 n/N】；全部完成时写【全部完成】。）"

# 开放式推理：事先不知道要做多少轮（如持续全网检索、开放式改进），不要求列出总步数。
DEFAULT_PROTOCOL_OPEN = """【协作协议·开放式】本任务是开放式的持续工作，事先无法确定总步数，我不会逐轮审阅。
1. 不必预先列出全部步骤。每轮推进一段合适的工作量（例如检索并整理一批来源、完成一处改进、写完一个小节），然后停下。
2. 每轮结束时，用一两句话写明本轮完成了什么、下一轮准备做什么，最后一行单独写：【本轮完成】
3. 确认已没有值得继续的内容时（目标已达成、来源或材料已用尽、再做只会重复），最后一行单独写：【全部完成】
4. 遇到必须由我决定的问题，最后一行写：【需要人类】并说明原因。
5. 可以自行决定的细节，请按你的最佳判断直接做，不要问我；不要重复已经完成的内容。"""

DEFAULT_REMINDER_OPEN = "（请记得：每轮结束时最后一行单独写【本轮完成】；确认没有可继续的内容时写【全部完成】。）"

DEFAULT_PHRASES = [
    "非常棒，让我们继续下一步。",
    "太好了，让我们继续。",
    "很好，请接着做下一步。",
    "做得很好，继续推进吧。",
    "非常好，按计划继续下一步。",
    "不错，请继续。",
    "很好，我们接着往下做。",
    "完全认同，继续下一步吧。",
    "思路很清晰，请继续推进。",
    "好的，保持这个质量，进入下一步。",
]

DEFAULT_TEMPLATES: dict[str, list[str]] = {
    # 模型报告了【步骤完成 n/N】
    "step": [
        "非常棒，让我们继续第 {next} 步。",
        "很好，请接着做第 {next} 步（共 {total} 步）。",
        "太好了，进入第 {next} 步吧。",
    ],
    # 开放式：模型报告了【本轮完成】
    "round": [
        "很好，按你上一轮写的计划继续下一轮。",
        "做得不错，继续下一轮，不要重复已完成的内容。",
        "非常好，接着推进下一轮吧。",
        "很好，继续。仍有值得做的内容就接着做，确认做完了就写【全部完成】。",
    ],
    # 回答被截断
    "truncated": ["请接着上面未完成的内容继续写，不要重复已写部分。"],
    # 生成报错 / 回答为空
    "error": ["刚才似乎中断了，请从上一步结束的地方继续。"],
    # 无标记，以提问结尾
    "question": [
        "你来判断，按你认为最好的方案直接做，然后继续。",
        "按你推荐的做法来，直接继续。",
    ],
    # 【需要人类】且配置为自动采纳
    "need_human_auto": ["按你推荐的方案继续。"],
}

# 默认只收“命令式”的高危操作，避免写作类长文里出现“支付”“删除”等普通词就误停。
# 如果被续推的模型能操作电脑或账户（例如接了动作生成器），可在 runner.yaml 的 behaviour.danger_keywords 里加上
# “付款”“支付”“转账”“永久删除”等。
DEFAULT_DANGER_KEYWORDS = [
    "rm -rf", "rm -fr", "del /f", "del /s", "rmdir /s", "rd /s", "format c:", "Remove-Item -Recurse",
    "DROP TABLE", "DROP DATABASE", "TRUNCATE TABLE", "shutdown /", "mkfs.", "dd if=",
]

DEFAULT_ERROR_PATTERNS = [
    r"Something went wrong", r"出了点问题", r"出错了", r"网络错误", r"Network error", r"请重试",
    r"An error occurred", r"生成失败", r"服务异常",
]

DEFAULT_LIMIT_PATTERNS = [
    r"已达到.{0,12}上限", r"额度.{0,8}(用完|用尽|不足)", r"usage limit", r"rate limit", r"Too many requests",
    r"服务器繁忙", r"系统繁忙", r"请稍后再试", r"You've reached", r"limit reached",
]

DEFAULT_LOGIN_PATTERNS = [r"登录", r"Sign in", r"Log in", r"验证码", r"captcha", r"verify you are human"]


# ---------------------------------------------------------------- 站点画像

class Timing(BaseModel):
    settle_ms: int = 3000          # 文本稳定窗口
    start_timeout_s: float = 45    # 注入后须开始生成的时限
    poll_ms: int = 500             # 采样间隔
    max_turn_min: float = 60       # 单轮最长时间（分钟）：超过后仍有输出就继续等，毫无动静才判卡死
    stall_min: float = 10          # 超过单轮最长时间后，连续这么久没有任何新输出，才判定卡死
    inject_retry: int = 3          # 注入失败重试次数
    idle_user_s: float = 10        # 桌面：用户最近 N 秒有键鼠操作则推迟注入
    auth_settle_ms: int = 1500     # 有网络流 + 数据层时，流结束后的确认等待（不再等页面文字稳定）
    data_poll_s: float = 2.0       # 数据层读取的最小间隔
    # 输入框里有不是运行器写的内容时：先观察，不立刻暂停
    input_stale_s: float = 90      # 内容这么久没变化，才认定没人在打字
    stray_max_chars: int = 3       # 没人在打字、且只有这么几个字符：视为误触的残留字符，清掉后继续
    input_wait_max_s: float = 600  # 一直有人在打字：最多等这么久，再暂停叫人


class Match(BaseModel):
    url_prefix: Optional[str] = None
    url_contains: Optional[str] = None
    process: Optional[str] = None          # 桌面：进程名，如 yuanbao.exe
    title_contains: Optional[str] = None   # 桌面：窗口标题关键字
    class_name: Optional[str] = None       # 桌面：窗口类名


class WebSelectors(BaseModel):
    input: str                                  # 输入框（textarea 或 contenteditable）
    composer_root: Optional[str] = None         # 对话输入区的根节点；指定后只认其中的输入框
    last_reply: str                             # 匹配所有模型回答，取最后一条
    send_button: Optional[str] = None
    stop_button: Optional[str] = None           # 仅在生成中可见
    user_message: Optional[str] = None          # 匹配所有用户消息，用于检测人类亲自发言
    turn_container: Optional[str] = None        # 回答所在的整轮容器（用于限定结束操作条的查找范围）
    reply_done_marker: Optional[str] = None     # 回答结束后才出现的操作条（复制 / 重新生成）
    newest_first: bool = False                  # 消息列表是“最新在前”的倒序结构（如 arena.ai 的 flex-col-reverse）


class UIASpec(BaseModel):
    control_type: Optional[str] = None   # Edit / Button / Document / List / Text / Pane / Group ...
    name: Optional[str] = None
    sub_name: Optional[str] = None
    automation_id: Optional[str] = None
    class_name: Optional[str] = None
    found_index: int = 1
    depth: int = 0                       # 0 = 不限深度


class DesktopUIA(BaseModel):
    input: UIASpec
    reply_area: UIASpec
    send_button: Optional[UIASpec] = None
    stop_button: Optional[UIASpec] = None


class VisionRegions(BaseModel):
    """相对窗口左上角的像素坐标 [left, top, right, bottom]。"""
    reply: list[int] = Field(default_factory=lambda: [0, 0, 0, 0])
    input: list[int] = Field(default_factory=lambda: [0, 0, 0, 0])
    button: list[int] = Field(default_factory=lambda: [0, 0, 0, 0])
    idle_button_hash: Optional[str] = None   # 校准时记录的“空闲态”按钮区感知哈希
    max_hash_distance: int = 6


class Network(BaseModel):
    stream_url_pattern: Optional[str] = None   # 生成接口（流式请求）URL 正则：请求在途 = 正在生成
    stream_method: Optional[str] = None        # 只认这个 HTTP 方法的请求，如 POST
    data_layer: Optional[Literal["chatgpt"]] = None   # 数据层驱动：流结束后读取会话数据确认完成并取完整原文


class Profile(BaseModel):
    id: str
    name: str = ""
    kind: Literal["web", "desktop"]
    verified: bool = False                 # 是否已在真实页面上实测通过
    match: Match = Field(default_factory=Match)
    # 网页 / Electron
    cdp_endpoint: Optional[str] = None     # 不填则用 runner.yaml 的 cdp_endpoint
    selectors: Optional[WebSelectors] = None
    network: Network = Field(default_factory=Network)
    # 桌面
    tier: Literal["auto", "cdp", "uia", "vision"] = "auto"
    launch_cmd: Optional[str] = None       # Electron 应用以调试端口启动的命令
    uia: Optional[DesktopUIA] = None
    regions: Optional[VisionRegions] = None
    # 通用
    input_method: Literal["insert_text", "value_setter", "clipboard", "uia_value"] = "insert_text"
    submit: Literal["click", "enter", "ctrl_enter"] = "click"
    timing: Timing = Field(default_factory=Timing)
    error_patterns: list[str] = Field(default_factory=lambda: list(DEFAULT_ERROR_PATTERNS))
    limit_patterns: list[str] = Field(default_factory=lambda: list(DEFAULT_LIMIT_PATTERNS))
    login_patterns: list[str] = Field(default_factory=lambda: list(DEFAULT_LOGIN_PATTERNS))
    notes: str = ""


# ---------------------------------------------------------------- 运行器配置

class ProtocolCfg(BaseModel):
    enabled: bool = True
    text: str = DEFAULT_PROTOCOL
    remind_after: int = 2          # 连续 N 轮无任何协议标记则在推进词后追加提醒
    reminder: str = DEFAULT_REMINDER
    open_text: str = DEFAULT_PROTOCOL_OPEN       # 开放式推理的协议
    open_reminder: str = DEFAULT_REMINDER_OPEN


class Limits(BaseModel):
    max_rounds: int = 40
    max_runtime_h: float = 6
    min_interval_s: float = 5
    repeat_threshold: float = 0.9
    repeat_rounds: int = 3
    backoff_s: list[float] = Field(default_factory=lambda: [60, 180, 600])
    open_max_rounds: int = 500         # 开放式推理的默认上限：可以跑一整晚甚至几天
    open_max_runtime_h: float = 72


class Behaviour(BaseModel):
    on_need_human: Literal["pause", "auto"] = "pause"      # 【需要人类】时：暂停叫人 / 自动采纳推荐方案
    on_question: Literal["continue", "pause"] = "continue"  # 无标记但以提问结尾时
    danger_check: bool = True
    danger_keywords: list[str] = Field(default_factory=lambda: list(DEFAULT_DANGER_KEYWORDS))
    auto_pause_on_human_message: bool = True


class NotifyCfg(BaseModel):
    beep: bool = True
    webhook: Optional[str] = None      # 暂停 / 结束时 POST JSON，可接天枢或动作打印机的消息通道


class ServerCfg(BaseModel):
    api_host: str = "127.0.0.1"
    api_port: int = 8765
    mcp_port: int = 8766


class RunnerConfig(BaseModel):
    cdp_endpoint: str = "http://127.0.0.1:9230"
    chrome_path: Optional[str] = None
    chrome_user_data_dir: str = "chrome-profile"
    output_dir: str = "output"
    profiles_dir: str = "profiles"
    hotkeys: bool = True
    phrases: list[str] = Field(default_factory=lambda: list(DEFAULT_PHRASES))
    templates: dict[str, list[str]] = Field(default_factory=lambda: {k: list(v) for k, v in DEFAULT_TEMPLATES.items()})
    protocol: ProtocolCfg = Field(default_factory=ProtocolCfg)
    limits: Limits = Field(default_factory=Limits)
    behaviour: Behaviour = Field(default_factory=Behaviour)
    notify: NotifyCfg = Field(default_factory=NotifyCfg)
    server: ServerCfg = Field(default_factory=ServerCfg)

    def resolve(self, p: str) -> Path:
        path = Path(p)
        return path if path.is_absolute() else app_root() / path


class SessionSpec(BaseModel):
    """启动一个续推会话的请求。"""
    profile_id: str = ""                   # 留空：按网址自动选择站点画像
    target: Optional[str] = None           # 网页：标签页 id；桌面：窗口句柄。为空则自动选第一个匹配的
    url: Optional[str] = None              # 直接给对话网址：在专用浏览器里找到这个页面，找不到就新开一个
    task: Optional[str] = None             # mode=new 时发送的任务说明
    mode: Literal["new", "takeover"] = "takeover"
    name: Optional[str] = None
    max_rounds: Optional[int] = None
    on_need_human: Optional[Literal["pause", "auto"]] = None
    protocol: Optional[bool] = None        # 覆盖是否注入推进协议
    task_mode: Literal["steps", "open"] = "steps"   # steps 有限步骤（【步骤完成 n/N】）；open 开放式（【本轮完成】）
    max_runtime_h: Optional[float] = None  # 覆盖最长运行时间（小时）
    max_turn_min: Optional[float] = None   # 覆盖单轮最长时间（分钟）；开发测试类任务一轮可能很久


# ---------------------------------------------------------------- 读写

def load_runner_config(path: Optional[str | Path] = None) -> RunnerConfig:
    p = Path(path) if path else app_root() / "runner.yaml"
    if not p.exists():
        return RunnerConfig()
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return RunnerConfig.model_validate(data)


def load_profiles(cfg: RunnerConfig) -> dict[str, Profile]:
    out: dict[str, Profile] = {}
    d = cfg.resolve(cfg.profiles_dir)
    if not d.exists():
        return out
    for f in sorted(d.glob("*.yaml")):
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            prof = Profile.model_validate(data)
            out[prof.id] = prof
        except Exception as e:  # 单个画像损坏不影响其余
            print(f"[画像] 跳过 {f.name}: {e}", file=sys.stderr)
    return out


def save_profile(cfg: RunnerConfig, profile: Profile) -> Path:
    d = cfg.resolve(cfg.profiles_dir)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{profile.id}.yaml"
    data = profile.model_dump(exclude_defaults=True, exclude_none=True)
    data.setdefault("kind", profile.kind)
    p.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return p
