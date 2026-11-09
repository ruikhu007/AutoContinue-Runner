# Security Policy / 安全策略

## Supported version / 受支持版本

The current `v0.4.1-bilingual.1` release is a **pre-release**, not a security-certified stable version.
当前 `v0.4.1-bilingual.1` 属于**预发布版**，并非经过完整安全认证的稳定版。

## Report vulnerabilities / 报告漏洞

**Do not open a public GitHub issue containing credentials, tokens, chat transcripts, or exploit details.**
请勿在公开 Issue 中提交凭据、令牌、聊天记录或可直接利用的漏洞细节。

Please contact the maintainer privately through their GitHub profile or an enabled private vulnerability-reporting channel. If no private channel is configured, open a public issue asking for a secure contact method **without revealing vulnerability details**.
请通过维护者 GitHub 主页的私有联系方式或仓库启用的私密漏洞报告渠道联系。如尚无私密渠道，可仅创建不包含漏洞细节的 Issue，要求提供安全联系方式。

## Deployment boundaries / 部署边界

- Keep the control API (`127.0.0.1:8765`) on loopback. It is **not authenticated**; never proxy, port-forward, tunnel, or expose it to a public or shared network.
- MCP (`127.0.0.1:8766`) uses a bearer token, but an internet-facing deployment needs TLS, access restrictions, token rotation, and independent security review.
- A Chrome/Edge debugging endpoint can expose authenticated browser sessions. Only use a dedicated browser profile and keep the debugging port on loopback; do not expose it to other machines.
- Never commit `chrome-profile/`, `output/`, `runtime/`, `mcp.token`, session transcripts, cookies, or secrets.
- Avoid unattended continuation for payments, deletion, external publication, account permissions, or other irreversible actions; require human review.

- 8765 控制 API **无身份认证**，只允许本机访问，严禁通过代理、端口转发或隧道暴露到公网或共享网络。
- 8766 MCP 虽使用 Bearer 令牌，公网访问仍需 TLS、访问限制、令牌轮换和独立安全审查。
- 浏览器 CDP 调试端口可能暴露已登录的会话，必须使用独立用户配置并限制为本机访问。
- 不要提交浏览器配置、输出记录、令牌、Cookie 或机密数据。
- 涉及付款、删除、对外发布、权限变更等不可逆操作时，必须由人类审批。

## Scope of verification / 验证范围

Published screenshots and prior runtime history are evidence of those sessions, not proof that this bilingual build has passed equivalent long-duration or security regression tests.
已发布截图和历史运行记录仅证明相应会话，不代表此次双语版本已通过同等时长的稳定性或安全回归测试。
