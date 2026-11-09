# 续推运行器 / AutoContinue Runner — 快速入门 / Quick Start

## 中文

1. 解压 ZIP 到固定文件夹，双击 `安装.bat` 安装。首次需要联网。
2. 双击 `启动续推运行器.bat`，在自动打开的专用浏览器中登录 ChatGPT、Claude 等网站。
3. 打开 `http://127.0.0.1:8765/`，在右上角选择「中文」或「English」。
4. 粘贴对话网址，选择有限步骤/开放式任务，设置轮次与时长，点击「挂载并启动」。
5. 随时暂停或恢复；出现 `【需要人类】`、风险提示或限额时请人工复核。
6. 详细内容请读 [中文使用手册](README.md)。

## English

1. Extract the ZIP into a permanent folder and double-click `安装.bat` (Install). Internet is needed initially.
2. Double-click `启动续推运行器.bat` (Launch). Log in to your AI websites inside the opened dedicated browser.
3. Open `http://127.0.0.1:8765/`. Use the **中文 / English** language picker in the top-right corner.
4. Paste a conversation URL, choose finite-step/open-ended mode, set limits, and click **Attach and start**.
5. Review any `【需要人类】` prompts, safety warnings, or resource limits before resuming.
6. See the [full English manual](README.en.md).

**Safety / 安全：** Never expose the unauthenticated local control API on port 8765 to the public network. Do not publish `chrome-profile/`, `output/`, `mcp.token`, or personal chat logs. / 切勿将 8765 控制端口公开到互联网，也不要上传个人浏览器配置、日志和令牌。
