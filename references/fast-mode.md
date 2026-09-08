# Fast 配置与验证

## 模型目录

Fast 表示同一模型的服务档位。以 `gpt-6-astra` 为例，标准与 Fast 请求均使用该 ID；模型选择器通过 `additional_speed_tiers` 和 `service_tiers` 显示可用速度。

同步规则：显式模型清单字段优先，其次采用原生缓存中同名模型的字段；无同名原生条目时使用空数组。`template_slug` 仅提供通用兼容字段，不用于推断速度能力。需要恢复被清空的原生 Fast 信息时，刷新原生模型缓存后运行 `sync`，无需创建别名。

```sh
python3 scripts/bridge.py sync --models gpt-6-astra
```

上述命令从仓库根目录运行，默认读取 Codex 根配置。若根配置未声明独立 `cli_proxy` Provider，使用已配置的隔离配置文件，例如 `--config "$HOME/.codex/cli-proxy.config.toml"`。核对预览后添加 `--apply`；同步保留个人显示策略，不切换默认模型或速度。

## 模式设置

本 Skill 默认不启用 Fast。安装和同步保留已有速度设置；仅在用户明确选择时启用单次或长期 Fast 模式。

Codex CLI 提供 `/fast on`、`/fast off`、`/fast status`。单次启动可使用：

```sh
codex -c 'features.fast_mode=true' -c 'service_tier="fast"'
```

需要长期默认启用时，将 `service_tier = "fast"` 与 `[features]` 下的 `fast_mode = true` 合入现有配置。此操作应由用户明确选择，不覆盖原有 Provider、登录或其他 feature 设置。[Codex 速度说明](https://learn.chatgpt.com/zh-Hans/docs/agent-configuration/speed)

## 转发与验证

Codex 的 Fast 设置映射为请求中的 `priority`。API Key 模式下，可按公开 Responses API 文档解释响应的 `service_tier`。[Responses API](https://developers.openai.com/api/reference/cli/resources/responses/methods/create)

ChatGPT 订阅认证采用不同的服务端路由语义。OpenAI 在 Codex 问题回复中明确说明：响应中的 `default` 不代表 Fast 被忽略，`service_tier` 不是该模式下可靠的端到端验证字段。不得将公开 API 的响应判断直接套用于订阅桥接。[OpenAI 回复](https://github.com/openai/codex/issues/14204#issuecomment-4033184620)

8318 透明代理仅替换认证头，保留请求正文；CPA 仍需保留并传递 `service_tier`。不同 CPA 版本对 `fast` 的处理可能不同，验证 Codex 通路时应检查实际发出的 `priority`，不在透明代理中统一强制加速所有模型。CPA 维护者确认 SSE 与 WebSocket 均支持 Priority，不应仅为 Fast 强制更换传输协议。[CPA 回复](https://github.com/router-for-me/CLIProxyAPI/issues/4586#issuecomment-5096157843)

```sh
python3 scripts/bridge.py probe --desktop --models gpt-6-astra,gpt-5.6-sol,gpt-5.6-terra,gpt-5.6-luna --fast
```

验证分为三层：

| 层级 | 证据 |
| --- | --- |
| 目录声明 | 目标模型包含 Fast 元数据，其他模型未被误赋能力 |
| Codex 请求完成 | 单次子进程启用 Fast，指定模型完成预期响应 |
| 上游观察 | 记录响应档位并区分 API Key 与 ChatGPT 订阅语义，不单凭订阅响应的 `default` 判失败 |

CLI 探针不暴露原始上游响应时，结果中的 `served_service_tier` 为 null。验证订阅 Fast 接入时，结合原生目录能力、实际请求中的 `priority`、未被代理改写的传输和任务完成结果；速度收益另需同模型、同推理档位、多轮交替的测量，不以一个短请求的耗时证明固定倍速。
