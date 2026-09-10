# Codex CLI Model Bridge

面向 CLIProxyAPI（CPA）的 Codex 模型接入与配置维护 Skill，支持 Coding Plan／订阅模型接入、Provider 检查、模型目录同步、本地透明代理和调用验证。

当前版本：[V0.0.2](https://github.com/Eason412/codex-cli-model-bridge/releases/tag/V0.0.2) · [更新日志](changelogs/V0.0.2.md)

本项目基于 [Zhijian Skills 的 codex-cli-model-bridge](https://github.com/zjp1997720/zhijian-skills/tree/main/skills/codex-cli-model-bridge) 二次开发。

Skill 负责配置维护，透明代理和 CPA 负责运行时请求转发。默认请求链路：

```text
Codex → 本地透明代理 → CLIProxyAPI → Coding Plan／订阅模型服务
           8318            8317
```

上图端口为脚本默认值。模型目录负责选择器中的名称和参数，CPA 根据请求中的模型 ID 连接上游。

## 模型发现与目录同步

通过 CPA 的 `/v1/models` 接口核对可用模型，结合适配清单补充上下文窗口、输入模态和推理档位，生成 Codex 模型目录。内置适配清单如下；新增型号的清单格式见 [模型清单说明](references/model-manifests.md)。

| 模型系列 | 内置适配清单 |
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
- **调用验证**：文本响应、Shell 工具事件与多工具调用顺序检查。
- **GPT Fast 支持**：同名原生模型的速度档位继承、Fast 单次探测与服务档位转发检查。
- **子代理兼容检查**：V2 任务正文传递探测，以及原生 spawn、上下文继承和模型身份的专项验收说明。
- **配置变更保护**：预览摘要校验、配置备份与服务重启分离，保留现有进程管理方式。

## 环境准备

需要 Python 3.11+、已安装的 Codex CLI，以及已配置上游认证、可在本机访问的 CLIProxyAPI。透明代理模式另需 Node.js。

Python 主脚本使用标准库；仓库不包含 CPA 服务、WorkBuddy 插件或上游账号配置。CPA 的安装与模型接入见 [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI)。

### CPA 安装与初始化

以下流程适用于首次安装；已有 CPA 环境保留当前服务管理方式。

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

## 安装与调用

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

### 单一源码维护

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
| `~/.config/codex-cli-model-bridge/enabled-manifests.json` | 本机启用的内置清单，存在时全量同步只处理列出的模型 |
| Codex / CPA 各自的配置与认证目录 | 登录、密钥及运行配置 |

个人策略使用完整 JSON，包含 `protected_native_model_ids` 和 `hidden_native_model_ids`。临时指定另一份策略可用 `sync --catalog-policy <path>`，该参数优先级最高。

## 常用命令

以下命令在本仓库根目录执行。

### GPT Fast 模式

Fast 默认关闭，按需显式启用。安装与模型目录同步只提供 Fast 能力声明，不自动开启加速，也不覆盖已有的 Codex 速度设置。

Fast 沿用原模型 ID，通过服务档位启用。目录同步保留 GPT-6 Astra、GPT-5.6 Sol、Terra、Luna 等同名原生模型的 Fast 元数据，不按 GPT 前缀批量赋予能力，也不生成重复的 `*-fast` 模型。

Codex CLI 中的模式切换与状态检查：

```text
/fast on
/fast off
/fast status
```

单次 Fast 调用验证：

```sh
python3 scripts/bridge.py probe --desktop --models gpt-6-astra --fast
```

探测仅对当前子进程启用 Fast，不修改全局配置。Fast 会增加对应上游的额度消耗或费用；模型、账号与区域支持以实际服务为准。目录同步、持久配置及实际响应档位的区别见 [Fast 配置与验证](references/fast-mode.md)；功能说明见 [OpenAI 官方文档](https://learn.chatgpt.com/zh-Hans/docs/agent-configuration/speed)。

Sol、Terra、Luna 分别使用 `gpt-5.6-sol`、`gpt-5.6-terra`、`gpt-5.6-luna`；`--models` 支持逗号分隔的多个 ID。ChatGPT 订阅桥接中的响应档位回显不等同于公开 API 的计费档位判断，不以 `default` 回显单独判定 Fast 失效。

### 配置检查

```sh
python3 scripts/bridge.py audit
```

输出配置、目录和连接检查结果。代理配置未被自动发现时，使用 `--proxy-config` 指定实际路径；完整选项见各子命令的 `--help`。

### 独立配置与目录同步预览

```sh
python3 scripts/bridge.py configure
python3 scripts/bridge.py sync
```

确认预览结果后，分别添加 `--apply` 应用。`configure` 生成独立配置，`sync` 写入模型目录。具体流程见 [Skill 操作说明](SKILL.md)。

### 桌面透明代理配置预览

```sh
python3 scripts/bridge.py configure-desktop
```

该模式会调整 Codex 根配置并启动本地代理。应用时需要预览返回的 `--expected-sha256`，步骤见 [桌面桥接流程](SKILL.md#5-enable-transparent-desktop-coexistence)。

### 模型调用验证

将 `<model-id>` 替换为 CPA 中已配置的模型 ID：

```sh
python3 scripts/bridge.py probe --desktop --models "<model-id>"
```

独立配置模式省略 `--desktop`。探测会实际调用模型，消耗对应服务额度；`--shell` 检查真实命令事件，`--tool-sequence` 检查连续工具调用。

`--catalog` 指定的目录会实际传入 Codex。`--config` 仅接受当前 Codex 根配置路径，其他文件会明确报错；使用已安装的命名配置时，省略 `--desktop` 并传 `--profile <name>`。

### 子代理任务传递验证

以下命令通过透明代理发送合成的 V2 任务，要求完成响应中的随机标记与任务正文一致：

```sh
python3 scripts/bridge.py probe-multi-agent --models "<model-id>"
```

输出中的 `probe_scope` 为 `synthetic_agent_message_delivery`，`native_spawn_tested` 为 `false`。原生 spawn、角色覆盖、实际模型身份及 `fork_turns` 继承范围的验证步骤见 [子代理兼容与维护](references/spawn-compatibility.md)。

`configure-multi-agent` 提供兼容开关的配置预览；配置写入需要 `--apply` 与预览返回的 `--expected-sha256`。该命令不执行服务重启；运行程序、服务管理方式及本地补丁的核对要求见上述维护说明。

## 文件与扩展

| 路径 | 用途 |
| --- | --- |
| [SKILL.md](SKILL.md) | 配置、修复与验证流程 |
| [scripts/bridge.py](scripts/bridge.py) | 命令行维护入口 |
| [scripts/transparent_proxy.mjs](scripts/transparent_proxy.mjs) | 本地 HTTP / WebSocket 认证头转发 |
| [models/](models/) | 模型目录清单 |
| [policies/catalog.json](policies/catalog.json) | 原生模型保护和显示策略 |
| [tests/test_bridge.py](tests/test_bridge.py) | 隔离配置与测试替身回归 |

其他入口：[Windows 配置](references/windows.md) · [GLM Coding Plan](references/glm-coding-plan.md) · [故障排查](references/troubleshooting.md)。

## 开发检查

```sh
python3 -m unittest discover -s tests -v
node --check scripts/transparent_proxy.mjs
```

## 许可证

[MIT](LICENSE)。保留原有版权声明。

## 贡献与 PR

提交 PR 时说明问题、最小复现步骤、原因分析、解决思路和验证结果；模型接入类变更附 CPA 路由类型与脱敏探测结果。具体要求见 [贡献指南](CONTRIBUTING.md)，创建 PR 时会自动显示填写模板。
