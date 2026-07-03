# 安全政策 (Security Policy)

## 受支持的版本

| 版本 | 是否维护 |
|------|----------|
| 1.0.x | ✅ |

## 上报安全漏洞

**请不要公开提交 Issue 来报告安全漏洞。**

请通过 GitHub 的 **私密漏洞上报**功能提交：
仓库 → **Security** 标签 → **Report a vulnerability**
（若未开启，请先在仓库 Settings → Code security 中启用 Private vulnerability reporting）。

请在报告中尽量包含：

- 受影响的组件 / 端点 / 文件
- 复现步骤或 PoC
- 影响范围与你评估的严重程度
- 可能的修复建议（如有）

我们会尽快确认收到，并在核实后协调修复与披露。

## 部署侧安全建议

HKC 默认是**本地单机工具**，出厂配置面向本地开发：

- **鉴权**：公网/多人部署前，请设置环境变量 `HKC_API_KEY`（设置后所有请求需带
  `X-API-Key` 头；未设=开放）。
- **CORS**：默认放开所有来源；部署到受控网络时用 `HKC_CORS_ORIGINS`（逗号分隔）收紧。
- **破坏性操作**：清库端点 `POST /knowledge/reset` 不可逆，生产环境务必配合 `HKC_API_KEY` 保护。
- **密钥**：LLM API Key 等敏感信息切勿写入仓库；用环境变量或界面「连接设置」运行时注入。
