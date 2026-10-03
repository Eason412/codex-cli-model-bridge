# Codex CLI Model Bridge

中文 | [English](README.en.md)

**在 Codex 中直接使用 Coding Plan 与订阅模型。** 本工具将 Codex 接入本机运行的 CLIProxyAPI（以下简称 CPA），使 CPA 支持的模型共用 Codex 模型选择器，且不影响 ChatGPT 登录、账号功能与历史任务。新模型自动同步，无需修改代码或等待新版本。

- **Coding Plan 与订阅**：厂商面向编程工具推出的套餐与模型订阅，国内外均支持，例如国内的 Kimi、智谱 GLM 套餐，海外的 ChatGPT、Gemini 订阅。
- **CPA**：本机运行的开源代理服务，负责登录上述账号并转发请求。

当前版本：[V0.0.5](https://github.com/Eason412/codex-cli-model-bridge/releases/tag/V0.0.5) · [更新日志](changelogs/V0.0.5.md)。本项目基于 [Zhijian Skills 的 codex-cli-model-bridge](https://github.com/zjp1997720/zhijian-skills/tree/main/skills/codex-cli-model-bridge) 二次开发。

> ⚠️ **前提：本机需运行 CPA。** 本工具负责将 Codex 接入 CPA，不替代 CPA；尚未安装时，安装手册会先引导完成 CPA 的安装。

## ✨ 特点

- 🔄 **新模型自动同步**：模型列表直接取自 CPA。配合每日定时同步，CPA 新增的模型次日即出现在 Codex 模型选择器中，无需修改代码、编写配置或等待新版本。
- 🔐 **登录与历史任务完整保留**：常见接入方式需要将 Codex 的模型服务商切换为第三方，原有历史任务随之从列表中消失。本工具在 macOS 上保持服务商为 OpenAI，仅将请求地址指向本机代理，ChatGPT 登录、账号功能与历史任务均不受影响。
- 🎛️ **差异化个人设置**：只需在个人设置文件中填写与默认值不同的字段，一行即可隐藏模型或调整默认推理强度，支持 `gpt-image-*` 通配符；未设置的参数沿用 CPA 默认值。
- 🤖 **第三方模型可作子代理**：Codex 原生子代理的任务可交由 Kimi、Gemini 等 CPA 模型执行。本工具提供 `configure-multi-agent`（预览并写入 CPA 的多代理兼容开关）与 `probe-multi-agent`（检查子任务消息能否送达），详见[子代理兼容性](references/spawn-compatibility.md)。
- 🛡️ **写入校验与备份回退**：新模型目录须通过 Codex 自身解析检查才会写入；Codex 主配置改动先预览、经确认后写入；主配置改动前自动备份，模型目录保留上一版，均可随时恢复。CPA 密钥不写入 Codex 配置，也不出现在输出中。

## 🧩 支持的模型

CPA 支持的模型均可接入，本工具不限定模型：原生 Codex 模型沿用官方参数，其他模型由 CPA 生成参数。下表仅为常见模型系列的示例；实际可用的模型以 CPA 返回的列表为准，CPA 支持的新模型会自动出现。

| 模型系列 | 示例模型 | CPA 接入方式 |
| --- | --- | --- |
| GPT（OpenAI） | GPT-6 Astra、GPT-6.1 Sol、GPT-6 Luna | ChatGPT／Codex 账号 |
| Gemini | Gemini 3.8 Flash、Gemini 3.1 Pro Preview | Gemini CLI、AI Studio、Vertex AI、Antigravity、Gemini API Key |
| Kimi | Kimi K3、Kimi K2.8 Preview | Kimi Code 套餐或 API Key |
| Grok（xAI） | Grok 4.7 | xAI |
| DeepSeek | DeepSeek V4.1 Flash | OpenAI 兼容接口 |
| 智谱 GLM | GLM-5.3 | OpenAI 兼容接口 |
| 其他 OpenAI 兼容服务 | 如 OpenRouter 提供的模型 | OpenAI 兼容接口 |

OpenAI 兼容接口在 CPA 配置的 `openai-compatibility` 中添加。

## ⚙️ 工作原理

```text
Codex ──► 本机透明代理 ──► CPA ──► 各家 Coding Plan 或订阅服务
          127.0.0.1:8318    127.0.0.1:8317
```

| 组成 | 作用 |
| --- | --- |
| 透明代理 | 本工具安装的本机服务。接收 Codex 的请求，替换为 CPA 密钥后原样转发，不改动请求内容 |
| CPA | 按模型名将请求路由到对应的账号与服务商 |
| 模型目录 | Codex 模型选择器读取的 JSON 文件，由本工具根据 CPA 的实时模型列表生成，并叠加个人设置 |

日常请求仅经过透明代理与 CPA；本工具的命令行只在安装、同步与检查时运行。图中端口为默认值。

| 接入方式 | 适用平台 | 特点 |
| --- | --- | --- |
| 桌面透明代理 | macOS（默认） | 保持 ChatGPT 登录，桌面版与命令行通用 |
| 独立配置 | Windows（默认），其他平台备选 | 独立的 Codex 配置文件，通过 `codex --profile cli-proxy` 启动，主配置不受影响 |

## 🚀 设置方法

由 Agent 读取 [SETUP.md](SETUP.md)，按其中步骤完成安装、配置与验收。完成后重启 Codex，新模型即出现在模型选择器中。

需用户亲自完成的事项：

- 在 CPA 中登录 Coding Plan 或订阅账号（通常需在浏览器中操作）。
- macOS 上保持 Codex 的 ChatGPT 登录。

### 个人设置

| 设置项 | 位置 | 说明 |
| --- | --- | --- |
| 隐藏模型、默认推理强度 | `~/.config/codex-cli-model-bridge/overrides.json` | 只写与默认值不同的字段，支持通配符 |
| 统一上下文长度 | Codex `config.toml` 中的 `model_context_window` | 超过模型自身上限时取该上限；上下文达到窗口的 90% 时自动压缩 |
| 补充模型 | `~/.config/codex-cli-model-bridge/models.d/` | 仅用于 CPA 列表中缺失的模型 |

字段写法见 [覆盖与补充模型](references/model-manifests.md)。

## 📁 仓库结构

| 路径 | 用途 |
| --- | --- |
| [SETUP.md](SETUP.md) | 面向 Agent 的分步安装手册 |
| [SKILL.md](SKILL.md) | 面向 Agent 的维护、修复与安全规则 |
| [scripts/bridge.py](scripts/bridge.py) | 命令行入口 |
| [scripts/transparent_proxy.mjs](scripts/transparent_proxy.mjs) | 透明代理 |
| [references/](references/) | Fast、子代理、Windows、GLM、故障排查等专题 |
| [tests/](tests/) | 回归测试 |

## 🤝 贡献须知

欢迎提交兼容修复、功能改进与文档修订。提交 PR 前请注意：

- **范围**：一个 PR 只解决一个问题，不夹带个人配置或无关的格式调整。
- **说明**：写明问题与最小复现、原因与解决思路，以及实际执行过的验证；未执行的检查注明原因，不标记为通过。
- **测试**：在临时的 HOME 与 CODEX_HOME 下运行，不读写真实配置，不启停真实的 CPA 或代理。
- **模型相关变更**：附模型 ID、元数据来源、CPA 上游类型及脱敏后的验证结果。
- **信息保护**：提交前检查 diff、日志与截图，去除密钥、OAuth 数据、私人路径与内网地址。
- **模型 ID**：仓库不内置模型 ID，个人偏好保存在仓库之外。

完整要求见 [贡献指南](CONTRIBUTING.md)，创建 PR 时自动带出模板。

## 📄 许可证

[MIT](LICENSE)，保留原有版权声明。
