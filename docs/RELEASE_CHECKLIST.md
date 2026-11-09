# Release Acceptance Checklist / 发布验收清单

Status: **v0.4.1-bilingual.1 pre-release / 预发布版**. Items below are gates for a future stable release, **not claims of completed testing**.
状态：**v0.4.1-bilingual.1 预发布版**。下列项目是未来正式稳定版的验收条件，**不表示已完成测试**。

## Repository / 仓库
- [x] Public repository on GitHub / GitHub 公开仓库
- [x] English and Chinese user manuals / 中英文使用手册
- [x] MIT license / MIT 开源许可证
- [x] Two documented real-running screenshots / 两张有来源说明的真实运行截图
- [ ] Ensure Release assets, archive content, and tag commit are independently matched / 独立核对 Release 附件、压缩包内容和标签提交
- [ ] Publish SHA-256 in the GitHub Release notes and verify it on a fresh download / 在 Release 页面公布 SHA-256，并重新下载核验

## Installation / 安装
- [ ] Fresh Windows 10 install / Windows 10 全新环境安装
- [ ] Fresh Windows 11 install / Windows 11 全新环境安装
- [ ] Clean uninstall and reinstall / 卸载与重装
- [ ] Verify checksum and fallback package mirrors / 校验哈希与备用镜像源
- [ ] Validate no user configuration is included in downloadable ZIP / 确认 ZIP 不含用户配置或凭据

## Functional regression / 功能回归
- [ ] Switch both dashboard languages and inspect all controls / 检查所有控件的中英文切换
- [ ] Windows Tk console language parity / Windows Tk 控制台语言一致性
- [ ] ChatGPT and Claude ordinary 3-round continuation / ChatGPT 和 Claude 各完成普通三轮续推
- [ ] Finite-step and open-ended modes / 有限步骤与开放模式
- [ ] Completion and human-intervention markers / 全部完成与需要人类标记
- [ ] Pause, resume, human takeover, and unsent-draft preservation / 暂停恢复、人类接管及未发送草稿保护
- [ ] Verify no duplicate continuation on slow-stream response / 慢速流式输出不重复注入
- [ ] MCP session ownership and resume authorization / MCP 会话所有权和恢复权限
- [ ] At least one supervised 24-hour bilingual-build endurance test / 双语版至少一次有人监督的24小时耐久测试

## Security / 安全
- [ ] Verify 8765 cannot be reached from LAN or public internet / 确认8765无法从局域网或公网访问
- [ ] Ensure browser debugging ports are bound to loopback only / 确认浏览器调试端口只监听本机
- [ ] Test cross-origin write protection and localhost rebinding edge cases / 测试跨源写入保护和本地重绑定边界
- [ ] Review all included dependencies and bundled binary wheel / 检查依赖和内置二进制 wheel
- [ ] Scan repository and ZIP for tokens, cookies, personal data / 扫描仓库和压缩包中的敏感信息
- [ ] Require human authorization for irreversible external actions / 不可逆外部行动需人类授权

## Publication / 对外发布
- [ ] Define accurate product description and compatibility matrix / 完善产品介绍和兼容性矩阵
- [ ] Add private vulnerability reporting channel / 配置私密漏洞报告渠道
- [ ] Add issue templates and contributor instructions / 补充 Issue 模板和贡献说明
- [ ] Only then promote a tested release to stable / 完成验收后才发布稳定版

**Operational rule:** A model's `【全部完成】` marker confirms it declared completion, not that the real-world outcome was verified.
**操作原则：** 模型输出`【全部完成】`仅是自报完成，不等于现实成果已验收。
