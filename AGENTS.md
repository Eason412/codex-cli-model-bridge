# codex-cli-model-bridge 项目规则

本仓库是公开的 Codex 接入 CPA 工具：`scripts/bridge.py` 负责安装配置、按 CPA 实时列表生成模型目录、探针和审计，`scripts/transparent_proxy.mjs` 是 8318 透明代理。贡献、验证与发布流程见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 接口稳定性

`sync` 的参数（`--apply --config --catalog --codex`）、isolated-profile 的 `auth.command` 认证方式，以及输出 JSON 中的 `status`、`changes`、`source`、`error` 字段会被外部定时脚本读取，视为对外接口。改动它们时在版本日志中写明，并保持向后兼容或同时给出迁移说明。

## 修改与验证

- 先看 Git 状态，保留已有未提交改动。
- 测试按 CONTRIBUTING 的隔离要求执行：临时 `HOME`、`CODEX_HOME`、`UV_CACHE_DIR`，不对真实 `~/.codex` 运行 `install`、`restore`、`probe`，不启动、重启或修改真实 CPA 和 LaunchAgent。
- 行为修复补回归测试，不靠改测试预期掩盖回归；中英文 README 同步。

## 提交与发布

验证通过后提交并推送 `main`。版本日志、tag 与 GitHub Release 按 CONTRIBUTING 的「版本发布」执行，只在用户要求发布时进行。
