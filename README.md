# Codex CLI Model Bridge

用于配置和维护 Codex 与 CLIProxyAPI（CPA）连接的 Skill，提供 Provider 检查、模型目录同步、本地透明代理和调用验证工具。

Skill 负责搭建与维护；运行时由代理和 CPA 转发请求：

```text
Codex → 本地透明代理 → CLIProxyAPI → 模型服务
           8318            8317
```

上图端口为脚本默认值。模型目录负责选择器中的名称和参数，CPA 根据请求中的模型 ID 连接上游。

## 主要功能

- **配置检查**：检查 Codex 配置、Provider 与历史任务归属、模型目录和代理连接。
- **独立配置**：生成 CLIProxyAPI 专用配置与凭据读取助手，保留原有 Codex 配置。
- **桌面桥接**：配置本地认证头转发代理，保留原有 OpenAI Provider 身份和登录。
- **模型目录管理**：按模型清单同步目录，维护受管条目与显示策略。
- **调用验证**：通过 Codex 检查文本响应、Shell 工具事件及多工具调用顺序。

## 环境准备

需要 Python 3.11+、已安装的 Codex CLI，以及已配置上游认证、可在本机访问的 CLIProxyAPI。透明代理模式另需 Node.js。

Python 主脚本使用标准库；仓库不包含 CPA 服务、WorkBuddy 插件或上游账号配置。CPA 的安装与模型接入见 [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI)。

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
python3 scripts/bridge.py probe --desktop --models <model-id>
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
