# Codex CLI Model Bridge

将 CLIProxyAPI（CPA）中已配置的 Coding Plan／订阅账号模型接入 Codex 的 Skill，提供 Provider 检查、模型目录同步、本地透明代理和调用验证工具。

基于 [zjp1997720/zhijian-skills 的 codex-cli-model-bridge](https://github.com/zjp1997720/zhijian-skills/tree/main/skills/codex-cli-model-bridge) 维护，保留原作者 Zhijian AI 的 MIT 版权声明。本仓库补充模型清单、仓外个人策略、中文使用说明和贡献流程；CPA 桥接能力原本就包含在上游实现中。

上游文档见 [中文说明](https://github.com/zjp1997720/zhijian-skills/blob/main/docs/skills/codex-cli-model-bridge/README.zh-CN.md)。不使用 CPA、但已有 Codex Router 的用户，可查看上游的 [GLM Coding Plan / Router 路径](https://github.com/zjp1997720/zhijian-skills/blob/main/skills/codex-cli-model-bridge/references/glm-coding-plan.md)。

Skill 负责搭建与维护；运行时由代理和 CPA 转发请求：

```text
Codex → 本地透明代理 → CLIProxyAPI → Coding Plan／订阅模型服务
           8318            8317
```

上图端口为脚本默认值。模型目录负责选择器中的名称和参数，CPA 根据请求中的模型 ID 连接上游。

## 模型支持

仓库内置以下模型清单；同系列新增型号可以按 [模型清单说明](references/model-manifests.md) 扩展。

| 系列 | 内置模型 ID |
| --- | --- |
| Kimi | `kimi-k3` |
| Gemini | `gemini-3.7-flash`、`gemini-3.8-flash` |
| DeepSeek | `deepseek-v4-pro`、`deepseek-v4-flash` |
| Grok | `grok-4.6` |
| GPT | `gpt-6-astra`；其他原生条目从 Codex 模型缓存继承 |

通过仓外 `models.d/` 扩展清单，还可配置以下 CPA 插件路由：

| 系列 | 模型 ID | 接入方式 |
| --- | --- | --- |
| GLM | `glm-5.3-flash` | WorkBuddy 账号 → CPA 插件 |
| 混元 | `hy4-preview` | WorkBuddy 账号 → CPA 插件 |

Coding Plan／订阅认证和额度由 CPA 及对应上游处理。具体计划与授权方式取决于配置的上游：例如 DeepSeek 清单使用 OpenCode Go 路由描述，GLM Coding Plan 的配置见 [接入参考](references/glm-coding-plan.md)。Skill 使用 CPA 暴露的模型 ID，不保存上游账号凭据。

模型可用性以 CPA 的 `/v1/models` 和实际 Codex 探测结果为准；表中列出的是已提供清单或扩展配置的型号。

## 主要功能

- **配置检查**：检查 Codex 配置、Provider 与历史任务归属、模型目录和代理连接。
- **独立配置**：生成 CLIProxyAPI 专用配置与凭据读取助手，保留原有 Codex 配置。
- **桌面桥接**：配置本地认证头转发代理，保留原有 OpenAI Provider 身份和登录。
- **模型目录管理**：按模型清单同步目录，维护受管条目与显示策略。
- **调用验证**：通过 Codex 检查文本响应、Shell 工具事件及多工具调用顺序。

## 环境准备

需要 Python 3.11+、已安装的 Codex CLI，以及已配置上游认证、可在本机访问的 CLIProxyAPI。透明代理模式另需 Node.js。

Python 主脚本使用标准库；仓库不包含 CPA 服务、WorkBuddy 插件或上游账号配置。CPA 的安装与模型接入见 [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI)。

### 尚未安装 CPA

1. **安装程序。** macOS 已安装 Homebrew 时，在终端执行：

   ```sh
   brew install cliproxyapi
   ```

   Windows 可下载 [官方发行版](https://github.com/router-for-me/CLIProxyAPI/releases)；Linux 安装器、AUR 和其他安装方式见 [CPA 官方快速开始](https://help.router-for.me/cn/introduction/quick-start)。

2. **配置本机访问与上游账号。** 按 [基础配置](https://help.router-for.me/cn/configuration/basic) 设置 `host: "127.0.0.1"`、`port: 8317`，保持远程管理关闭，并在 `api-keys` 中设置自己的客户端访问密钥。Homebrew 服务默认读取 `$(brew --prefix)/etc/cliproxyapi.conf`。再按 CPA 文档中对应提供商的说明完成 Coding Plan／订阅认证；插件路由还需安装相应插件。

3. **启动服务。** macOS 配置完成后执行：

   ```sh
   brew services start cliproxyapi
   ```

   Windows / Linux 按所选安装方式启动，并指定实际配置文件。确认服务正常启动，且经过客户端认证的 `/v1/models` 返回目标模型 ID。

4. **连接 Codex。** 获取本 Skill 后，从下方的 `audit` 开始，预览配置、同步所需模型并执行探测。CPA 客户端密钥由本机凭据助手读取，账号配置无需复制到本仓库。

## 获取与使用

```sh
git clone https://github.com/Eason412/codex-cli-model-bridge.git
cd codex-cli-model-bridge
python3 scripts/bridge.py --help
```

Windows 可将 `python3` 替换为 `py -3`。命令成功后会显示可用子命令。

在 Codex 中引用本仓库的 [SKILL.md](SKILL.md)，例如：

```text
请按照此目录中的 SKILL.md 检查我的 Codex 与 CLIProxyAPI 配置。
先展示检查结果和修改计划，确认后再应用。
```

将本目录安装到所用 Codex 环境的 Skill 目录后，也可以用 `$codex-cli-model-bridge` 调用。

### 使用与维护同一份源码

macOS / Linux 可以将 Skill 入口链接到这个 Git 仓库。在仓库根目录执行，目标位置须尚未存在；已有安装先备份到 Skill 扫描目录之外：

```sh
mkdir -p "$HOME/.agents/skills"
ln -s "$PWD" "$HOME/.agents/skills/codex-cli-model-bridge"
```

Codex 支持符号链接形式的 Skill 目录，见 [官方说明](https://learn.chatgpt.com/docs/build-skills)。之后直接在仓库中维护源码，提交、推送即可同步到 GitHub。

个人文件保持在仓库之外：

| 位置 | 内容 |
| --- | --- |
| `~/.config/codex-cli-model-bridge/catalog-policy.json` | 个人模型显示策略，存在时优先于仓库默认策略 |
| `~/.config/codex-cli-model-bridge/models.d/` | 本机扩展模型清单 |
| Codex / CPA 各自的配置与认证目录 | 登录、密钥及运行配置 |

个人策略使用完整 JSON，包含 `protected_native_model_ids` 和 `hidden_native_model_ids`。临时指定另一份策略可用 `sync --catalog-policy <path>`，该参数优先级最高。

## 常用命令

以下命令在本仓库根目录执行。

### 检查当前配置

```sh
python3 scripts/bridge.py audit
```

输出配置、目录和连接检查结果。代理配置未被自动发现时，使用 `--proxy-config` 指定实际路径；完整选项见各子命令的 `--help`。

### 预览独立配置与模型同步

```sh
python3 scripts/bridge.py configure
python3 scripts/bridge.py sync
```

确认预览结果后，分别添加 `--apply` 应用。`configure` 生成独立配置，`sync` 写入模型目录。具体流程见 [Skill 操作说明](SKILL.md)。

### 预览桌面透明代理配置

```sh
python3 scripts/bridge.py configure-desktop
```

该模式会调整 Codex 根配置并启动本地代理。应用时需要预览返回的 `--expected-sha256`，步骤见 [桌面桥接流程](SKILL.md#5-enable-transparent-desktop-coexistence)。

### 验证模型调用

将 `<model-id>` 替换为 CPA 中已配置的模型 ID：

```sh
python3 scripts/bridge.py probe --desktop --models "<model-id>"
```

独立配置模式省略 `--desktop`。探测会实际调用模型，消耗对应服务额度；`--shell` 检查真实命令事件，`--tool-sequence` 检查连续工具调用。

## 文件与扩展

| 路径 | 用途 |
| --- | --- |
| [SKILL.md](SKILL.md) | 配置、修复与验证流程 |
| [scripts/bridge.py](scripts/bridge.py) | 命令行维护入口 |
| [scripts/transparent_proxy.mjs](scripts/transparent_proxy.mjs) | 本地 HTTP / WebSocket 认证头转发 |
| [models/](models/) | 模型目录清单 |
| [policies/catalog.json](policies/catalog.json) | 原生模型保护和显示策略 |
| [tests/test_bridge.py](tests/test_bridge.py) | 隔离配置与测试替身回归 |

模型清单描述的是目录元数据，可用性以实际 CPA 路由和调用结果为准。新增条目见 [模型清单说明](references/model-manifests.md)，其他入口见 [Windows 配置](references/windows.md)、[GLM Coding Plan](references/glm-coding-plan.md) 和 [故障排查](references/troubleshooting.md)。

## 开发检查

```sh
python3 -m unittest discover -s tests -v
node --check scripts/transparent_proxy.mjs
```

## 许可证

[MIT](LICENSE)。保留原有版权声明。

## 贡献与 PR

提交 PR 时说明问题、最小复现步骤、原因分析、解决思路和验证结果；模型接入类变更附 CPA 路由类型与脱敏探测结果。具体要求见 [贡献指南](CONTRIBUTING.md)，创建 PR 时会自动显示填写模板。
