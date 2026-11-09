# Global Launch & Press Kit / 全球推广与媒体资料包

**Product:** AutoContinue Runner / AI 续推运行器  
**Repository:** https://github.com/ruikhu007/AutoContinue-Runner  
**Download:** https://github.com/ruikhu007/AutoContinue-Runner/releases  
**Version at initial launch:** v0.4.1-bilingual.1 **pre-release**  
**Platform:** Windows 10/11; Chrome or Edge for web chat workflows  
**License:** MIT  
**Website:** GitHub repository (no separate website is represented as official)

## Positioning / 一句话定位

**EN:** Turn AI chats into long-running agents — with a supervised, open-source continuation loop for existing AI web apps.

**中文：** 让普通 AI 对话拥有长任务持续推进能力——为已有 AI 网页应用提供开源、可监督的续推循环。

**Clarification / 严谨表述：** This is not a claim to be the first auto-continue tool in the world. It is a workflow combining multi-site profiles, state-aware continuation, safeguards, local records, and MCP integration. It does **not** guarantee nonstop autonomy, give an AI app additional tools, bypass platform restrictions, or make an unfinished result correct. Native desktop adapters exist but have not undergone the same live validation as the listed web profiles.

## What it does / 实际功能

The runner uses an authenticated **user browser session** rather than a separate model API key, then:
1. Observes a supported chat and decides when a response really finishes.
2. Uses completion, step, and human-intervention markers to decide whether to continue, stop, or pause.
3. Injects an appropriate follow-up only when allowed, and verifies the next round started.
4. Preserves logs; supports limits, pause/resume, manual takeover, web-dashboard controls, and an MCP control interface.

Supported/tested **web** profiles in prior environments include ChatGPT, Claude, and Arena. The package also includes Gemini and DeepSeek templates that need verification on the user's target environment. Website layouts and rules can change.

## English release announcement (for press and communities)

### Headline
AutoContinue Runner Open-Sources a Way to Keep AI Chat Tasks Moving on Windows

### Announcement
**November 9, 2026** — AutoContinue Runner, an MIT-licensed Windows project, is now available as a bilingual Chinese/English pre-release on GitHub.

Most AI chat workflows still stop when a response ends. For long projects, a human often has to return to the screen, decide whether to continue, and send the next prompt. AutoContinue Runner introduces a local supervision layer around existing signed-in AI web apps: detect that an answer is finished, evaluate continuation rules, send the next prompt when appropriate, and retain an audit trail.

The project includes tested web profiles for ChatGPT, Claude and Arena in earlier Windows environments, a local dashboard with Chinese/English switching, task protocols, pause and human-takeover controls, adjustable round/time limits, run history, and a token-protected MCP bridge for authorized AI agents. Some native desktop app adapters and additional site profiles remain experimental.

The goal is not simply to remove repeated clicks. It is to lower the operational friction between **chat-based AI applications** and **supervised, multi-round task execution** — without requiring a separate model API key for the continuation loop.

The code, manuals, real session screenshots, and Windows pre-release package are available at https://github.com/ruikhu007/AutoContinue-Runner .

**Important limitations:** The bilingual build remains a pre-release and has not completed long-duration regression testing. The software neither bypasses platform limits nor guarantees successful task outcomes. Users must follow provider policies and keep local control/debugging ports inaccessible from public networks.

### Media contact
Please contact the repository maintainer through the GitHub profile or project Issues. Do not send credentials or sensitive chat logs in public Issues.

## 中文发布通稿

### 标题
开源软件“AI续推运行器”发布：让普通 AI 网页应用持续推进多轮任务

### 正文
**2026年11月9日**，MIT 许可开源项目 **AutoContinue Runner（AI续推运行器）** 在 GitHub 公开发布 Windows 中英文双语预发布版本。

过去，普通网页 AI 对话在每轮回答结束后，往往仍需用户反复返回电脑输入“继续”。AI续推运行器在已登录的 AI 网页会话外构建本地续推监管层：识别模型回答是否真正结束、判断任务协议和暂停条件、在合适时推进下一轮，并保存过程记录。

该软件已包含此前环境中实测的 ChatGPT、Claude、Arena 网页画像，提供中英文切换的网页控制台、有限步骤和开放式续推、人类接管、轮次与时长限制、运行日志，以及可供授权智能体调用的 MCP 接口。部分桌面版适配功能和其他平台画像仍处于实验阶段。

其价值不在于机械输入“继续”，而在于减少普通 AI 应用从一次问答走向**受人监督的持续任务执行**过程中的人工衔接成本；续推循环本身无需单独配置大模型 API 密钥。

开源地址：https://github.com/ruikhu007/AutoContinue-Runner

**说明：** 当前为预发布版，尚未完成双语版本长时间回归测试；软件不能突破平台使用限制，也不能保证模型行动结果正确。使用时应遵守相关平台规则，不应将本地控制及浏览器调试端口暴露到公网。

## Product Hunt submission draft

- **Product name:** AutoContinue Runner
- **Tagline (60 characters max):** Keep AI chats working on longer tasks
- **Short description (260 characters max):** Open-source Windows companion for AI web chats. Detect finished responses, check stop and safety rules, and continue multi-round tasks in signed-in ChatGPT, Claude and Arena sessions. Local dashboard, history and MCP bridge. Pre-release.
- **Product URL:** https://github.com/ruikhu007/AutoContinue-Runner
- **Topics to consider:** Artificial Intelligence; Productivity; Open Source
- **Gallery:** Use genuine screenshots under `docs/screenshots/`, clearly labeled; do not suggest the bilingual build has passed the historical endurance run.
- **Maker introduction:** I built this to solve the repetitive human handoff between rounds of work in existing AI chat apps. The goal is supervised continuity, not unattended autonomy or bypassing any provider's limits. I'd particularly value feedback on where response-end detection fails, whether the safeguards are sufficient, and which web app to support next.

Product Hunt requires a human-owned account, working product description, and assets; see https://help.producthunt.com/en/articles/17350772-launch-guide . Do **not** solicit upvotes or purchase votes.

## Social posts (review platform rules before posting)

### X / Mastodon / Bluesky — English
What if a web AI chat didn't need you to type “continue” after every round?

We open-sourced **AutoContinue Runner**: a Windows companion that watches supported, signed-in AI chats, checks stop/safety rules, and advances multi-round tasks. Local dashboard + logs + MCP bridge.

MIT licensed · bilingual pre-release · no model API key needed for the continuation loop.

https://github.com/ruikhu007/AutoContinue-Runner

### LinkedIn — English
**AI assistants shouldn't need a human to press “continue” after every completed response.**

AutoContinue Runner is a new MIT-licensed, Windows-first open-source project aimed at bringing supervised task continuity to existing AI web applications.

It observes completed model responses, applies task and safety conditions, and sends the next prompt only when appropriate. Users can pause, intervene, inspect logs, or control authorized sessions through an MCP bridge. It has previously tested ChatGPT, Claude and Arena web profiles, with further integrations still experimental.

This is a pre-release, not a claim of universal autonomy or guaranteed outcomes. We're inviting technical review, bug reports, and contributors.

Project and documentation: https://github.com/ruikhu007/AutoContinue-Runner

### 中文社区
AI 的价值不应该停留在“一问一答”。我们开源了 Windows 软件「AI续推运行器」：在已登录的 ChatGPT、Claude 等网页对话旁监测回答完成状态，识别任务结束／需要人类的标记，在授权范围内自动推进下一轮，同时保留暂停、接管、日志与 MCP 调度能力。

不是无限制的“AI永动机”，也不是绕过大模型平台限制；核心是把普通 AI 应用的多轮任务持续性真正交还给用户。MIT 开源，中英文预发布版，欢迎测试反馈与贡献。

https://github.com/ruikhu007/AutoContinue-Runner

## Community strategy / 社区传播原则

1. **GitHub:** Official source, English-first landing, demos, docs, reproducible issues, and checks.
2. **Product Hunt:** Best after finalizing a short demo and ensuring the pre-release installs reliably on a clean Windows machine. Ask for feedback, **never upvotes**.
3. **Hacker News / Show HN:** Ask the human maker to write a genuine personal explanation in their own words; HN guidelines explicitly discourage generated copy and automated submissions: https://news.ycombinator.com/newsguidelines.html .
4. **Relevant Reddit communities:** First examine each community's self-promotion rules; contribute technical details and invite authentic discussion rather than mass-posting identical content.
5. **Open-source and AI builders:** Reach out selectively to maintainers and users of agent orchestration, MCP, browser automation, and desktop AI tools. Explain the actual technical differences, never claim an unverified “world first”.

### Suggested first-week measurement / 首周数据

Track unique GitHub visitors, release-asset downloads (if available), stars/forks, issues, valid installation reports, demo completion feedback, and contributors. Record dates and source channels; do not equate stars with active users.

### Next technical evidence / 下一步应补充的证据

- A short, unedited Windows walkthrough video: start, three completed continuation rounds, human-pause handling, and final stop.
- Fresh Windows 10/11 install checks and pre-release checksum confirmation.
- A real 24-hour monitored regression of **this bilingual build**, not merely the earlier version.
- Explicit side-by-side evidence for supported web sites and experimental desktop adapters.
- Clearly explained threat model and security review for local APIs and browser debugging ports.

## Competitive honesty / 竞品对照

There are earlier auto-continue/auto-resume products, including:
- ChatSequel: https://chromewebstore.google.com/detail/chatsequel-chatgpt-auto-c/cdkihfdcmchokmfbpeglffiiknmmjdcj
- Claude Auto-Continue: https://github.com/timothy22000/claude-autocontinue
- Claude & ChatGPT desktop auto-resume: https://github.com/MichalCholajczyk/claude_and_chatgpt-desktop-auto-resume

**Do not advertise “the first auto-continue application ever.”** Instead, compare on verified features: task protocol, response-end detection, agent/MCP integration, supervision, multi-site support, and reproducible long-run evidence. This comparison is descriptive, not a claim that the listed competitors lack every corresponding capability.
