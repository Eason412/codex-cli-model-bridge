# Codex CLI Model Bridge

让 Codex 通过本机 CLIProxyAPI（CPA）使用 Coding Plan／订阅模型的 Skill 与命令行工具：检查 Codex 配置和 Provider，同步模型目录，部署本地透明代理，并实际调用模型做验证。

当前版本：[V0.0.4](https://github.com/Eason412/codex-cli-model-bridge/releases/tag/V0.0.4) · [更新日志](changelogs/V0.0.4.md)

本项目基于 [Zhijian Skills 的 codex-cli-model-bridge](https://github.com/zjp1997720/zhijian-skills/tree/main/skills/codex-cli-model-bridge) 二次开发。

## 工作方式

本工具只维护配置；运行时的请求转发由透明代理和 CPA 完成。默认链路：

```text
Codex ──► 本地透明代理 ──► CLIProxyAPI ──► Coding Plan／订阅模型服务
          127.0.0.1:8318     127.0.0.1:8317
```

端口为默认值。模型目录决定 Codex 选择器里显示哪些模型及其参数；CPA 按请求中的模型 ID 连接上游。Codex 一个任务只用一个 Provider，目录条目不能把单个模型路由到另一个 Provider，所以接入时保持原有 Provider 身份，不改写历史任务。

| 模式 | 适用 | 做法 |
| --- | --- | --- |
| 桌面透明代理 | macOS 默认 | 保留 `model_provider = "openai"` 和 ChatGPT 登录，把 `openai_base_url` 指向本机代理；代理替换认证头后转发给 CPA |
| 隔离配置 | Windows 默认，其他平台备选 | 在 `cli-proxy.config.toml` 中单独配置 CPA Provider，用 `codex --profile cli-proxy` 调用，不改根配置 |

自定义 Provider 需要 Responses API，只支持 Chat Completions 的上游不够用。

## 支持的模型

通过 CPA 的 `/v1/models` 核对实际可用的模型 ID，再用适配清单补充上下文窗口、输入模态和推理档位，生成 Codex 模型目录。内置清单：

| 模型系列 | 内置适配清单 |
| --- | --- |
| Kimi | `kimi-k3` |
| Gemini | `gemini-3.7-flash`、`gemini-3.8-flash` |
| DeepSeek | `deepseek-v4-pro`、`deepseek-v4-flash` |
| Grok | `grok-4.6` |
| GPT | `gpt-6-astra`；其他原生条目从 Codex 模型缓存继承 |

新增型号的清单格式见 [模型清单说明](references/model-manifests.md)。Coding Plan／订阅的认证和额度由 CPA 及对应上游处理，本工具只使用 CPA 暴露的模型 ID，不保存上游账号凭据；GLM Coding Plan 的接入见 [接入参考](references/glm-coding-plan.md)。模型是否可用以 CPA 的 `/v1/models` 和实际探测结果为准。

## 环境准备

需要 [uv](https://docs.astral.sh/uv/getting-started/installation/)、Python 3.11+、已登录的 Codex CLI，以及已配置上游认证的 CLIProxyAPI；桌面透明代理模式另需 Node.js。Python 脚本只用标准库，依赖由 uv 按脚本头部声明处理。

### 首次安装 CPA

已有 CPA 的环境保留现有服务管理方式，跳过本节。

1. **安装。** macOS 用 Homebrew：

   ```sh
   brew install cliproxyapi
   ```

   Windows 下载 [官方发行版](https://github.com/router-for-me/CLIProxyAPI/releases)；Linux 和其他方式见 [CPA 快速开始](https://help.router-for.me/cn/introduction/quick-start)。

2. **配置。** 按 [基础配置](https://help.router-for.me/cn/configuration/basic) 设置 `host: "127.0.0.1"`、`port: 8317`，保持远程管理关闭，在 `api-keys` 中设置自己的客户端密钥。如需让局域网访问 CPA，必须保留客户端密钥；透明代理始终只监听本机。Homebrew 服务读取 `$(brew --prefix)/etc/cliproxyapi.conf`。然后按 CPA 文档完成对应上游的 Coding Plan／订阅认证。

3. **启动并确认。** macOS 执行 `brew services start cliproxyapi`；其他平台按安装方式启动。带客户端密钥请求 `/v1/models`，返回列表里应有目标模型 ID。

## 安装

```sh
git clone https://github.com/Eason412/codex-cli-model-bridge.git
cd codex-cli-model-bridge
uv run scripts/bridge.py --help
```

最后一条命令列出 `audit`、`configure`、`sync` 等子命令即为可用。下文的 `<skill-dir>` 指这个仓库的绝对路径，路径含空格时加引号；所有命令都不依赖当前工作目录。

作为 Skill 使用时，把仓库链接进 Codex 的 Skill 目录（目标位置须尚未存在），之后就能用 `$codex-cli-model-bridge` 调用，仓库里的修改直接生效：

```sh
mkdir -p "$HOME/.agents/skills"
ln -s "$PWD" "$HOME/.agents/skills/codex-cli-model-bridge"
```

Codex 支持符号链接形式的 Skill 目录，见 [官方说明](https://learn.chatgpt.com/docs/build-skills)。也可以直接让 Codex 读本仓库的 [SKILL.md](SKILL.md)，例如“按此目录的 SKILL.md 检查我的 Codex 与 CLIProxyAPI 配置，先给检查结果和修改计划，确认后再应用”。

## 快速开始（macOS 桌面透明代理）

所有写入命令不加 `--apply` 时只预览，不改任何文件。`<codex-home>` 默认是 `~/.codex`，设置了 `CODEX_HOME` 时用该目录。

1. **检查现状。**

   ```sh
   uv run <skill-dir>/scripts/bridge.py audit
   ```

   输出 `"status": "ready"` 表示配置、目录和路由都正常；`"attention"` 时按 `findings` 逐项处理。CPA 配置没被自动发现时用 `--proxy-config` 指定。

2. **建立隔离配置和凭据助手。** 模型同步的在线检查通过它读取 CPA 客户端密钥，密钥不会复制进 Codex 配置。

   ```sh
   uv run <skill-dir>/scripts/bridge.py configure
   uv run <skill-dir>/scripts/bridge.py configure --apply
   ```

3. **同步模型目录。** 预览会列出全目录的变更，确认除目标模型外其他条目不变后再应用。

   ```sh
   uv run <skill-dir>/scripts/bridge.py sync --config <codex-home>/cli-proxy.config.toml
   uv run <skill-dir>/scripts/bridge.py sync --config <codex-home>/cli-proxy.config.toml --apply
   ```

   只同步部分模型用 `--models <id1>,<id2>`。

4. **接入桌面透明代理。** 预览返回 `config_sha256`、`service_action`（`none` / `start` / `restart`）和运行文件的前后摘要；只显示受管的 `model`、`model_provider`、`model_catalog_json`、`openai_base_url` 四个字段，不打印其他配置和凭据。确认后带上摘要应用：

   ```sh
   uv run <skill-dir>/scripts/bridge.py configure-desktop
   uv run <skill-dir>/scripts/bridge.py configure-desktop --expected-sha256 <config_sha256> --apply
   ```

   摘要与当前文件不符时拒绝写入，需重新预览。配置、运行文件和启动项都没变且代理正常时，返回 `unchanged`，不会重启代理；`service_action` 为 `restart` 时，正在进行的 Codex 请求会短暂中断。

5. **实际调用验证。** 会消耗对应模型的额度：

   ```sh
   uv run <skill-dir>/scripts/bridge.py probe --desktop --models "<model-id>"
   ```

   结果中该模型 `"ok": true` 即接入完成。回答必须与预期标记完全一致才算通过。

Windows 和隔离配置模式省略第 4 步，探测时去掉 `--desktop`，用 `codex --profile cli-proxy` 使用；详见 [Windows 配置](references/windows.md)。

## 其他命令

| 命令（前缀 `uv run <skill-dir>/scripts/bridge.py`） | 用途 |
| --- | --- |
| `probe --desktop --shell --models <id>` | 要求模型真正执行一次 `pwd` |
| `probe --desktop --tool-sequence --models <id>` | 要求按顺序执行 `pwd` 和 `git --version` |
| `probe-multi-agent --models <id>` | 经透明代理发送合成的子代理任务，检查任务正文是否完整送达；不等于原生 spawn 验收，后者见 [子代理兼容与维护](references/spawn-compatibility.md) |
| `configure-multi-agent` | 预览 CPA 的 `codex.optimize-multi-agent-v2` 开关；应用同样需要预览摘要，不会重启 CPA |
| `restore-default` | 历史任务不可见时的修复：恢复历史上占多数的 Provider 和原生默认模型，移除根配置里的目录和地址覆盖；不是常规安装步骤 |
| `validate-manifest <path>` | 校验模型清单，不安装路由 |

GPT 的 Fast 档位默认不启用，目录继承、开关和单次探测见 [Fast 配置与验证](references/fast-mode.md)。各子命令的完整参数用 `--help` 查看；出问题时先看 [故障排查](references/troubleshooting.md)。

## 个人文件

个人偏好和运行状态保存在仓库之外：

| 位置 | 内容 |
| --- | --- |
| `~/.config/codex-cli-model-bridge/catalog-policy.json` | 个人模型显示策略（含 `protected_native_model_ids`、`hidden_native_model_ids`），存在时优先于仓库默认策略 |
| `~/.config/codex-cli-model-bridge/models.d/` | 本机扩展的模型清单 |
| `~/.config/codex-cli-model-bridge/enabled-manifests.json` | 本机启用的内置清单，存在时全量同步只处理列出的模型 |
| Codex / CPA 各自的配置和认证目录 | 登录、密钥及运行配置 |

`sync --catalog-policy <path>` 临时指定另一份策略，优先级最高；`--state-dir <path>` 把状态、个人策略、`models.d/` 和启用清单一起换到指定目录。

## 仓库结构

| 路径 | 用途 |
| --- | --- |
| [SKILL.md](SKILL.md) | 给 agent 的配置、修复和验证流程 |
| [scripts/bridge.py](scripts/bridge.py) | 命令行入口 |
| [scripts/transparent_proxy.mjs](scripts/transparent_proxy.mjs) | 本机 HTTP / WebSocket 认证头转发，并为旧版客户端补足 `Version` 头 |
| [models/](models/) | 内置模型清单 |
| [policies/catalog.json](policies/catalog.json) | 原生模型保护和显示策略 |
| [references/](references/) | Fast、子代理、Windows、GLM、旧版 Kimi 兼容和故障排查等专题 |
| [tests/](tests/) | 隔离配置与测试替身的回归测试 |

## 开发检查

先按 [贡献指南](CONTRIBUTING.md#验证) 设置临时的 HOME、CODEX_HOME 和输出目录，避免测试读写真实配置，再运行：

```sh
PYTHONDONTWRITEBYTECODE=1 uv run --no-project --python 3.11 python -m unittest discover -s tests -v
node --test tests/test_proxy_version.mjs
node --check scripts/transparent_proxy.mjs
```

## 贡献与许可证

提交 PR 时说明问题、最小复现、原因、解决思路和验证结果；模型接入类变更附 CPA 路由类型和脱敏后的探测结果。详见 [贡献指南](CONTRIBUTING.md)，创建 PR 时会自动带出模板。

许可证为 [MIT](LICENSE)，保留原有版权声明。
