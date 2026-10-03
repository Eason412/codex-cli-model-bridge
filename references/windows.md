# Windows notes

Isolated profile is the default Windows path. Homebrew, LaunchAgents, and Codex Router are optional.

## What to install

1. Codex CLI for Windows, already logged in if Desktop history should stay on ChatGPT.
2. uv and Python 3.11 or newer. Use uv for all bridge invocations; no Python interpreter fallback is needed.
3. Node.js only if the user wants Desktop-transparent mode.
4. CLIProxyAPI reachable through the local bridge endpoint. Practical sources:
   - [CLIProxyAPI GitHub Releases](https://github.com/router-for-me/CLIProxyAPI/releases)
   - [EasyCLIProxyAPI](https://github.com/router-for-me/EasyCLIProxyAPI) if a tray app is easier

Keep remote management disabled. CPA exposed on LAN requires a client API key; the transparent header proxy remains loopback-only. Do not rebind an existing shared CPA listener during bridge setup.

## Paths

| Role | Location |
| --- | --- |
| Codex home | `%USERPROFILE%\.codex` or `$env:CODEX_HOME` |
| Isolated profile | `%USERPROFILE%\.codex\cli-proxy.config.toml` |
| Bridge state | `%USERPROFILE%\.config\codex-cli-model-bridge` |
| Credential helper | `%USERPROFILE%\.config\codex-cli-proxy\read-client-key.py` |
| CLIProxyAPI config | `CLIPROXYAPI_CONFIG`, `%USERPROFILE%\.cli-proxy-api\config.yaml`, or the EasyCLIProxyAPI `cpa-core\config.yaml` |

Unix mode `0600` is not a Windows ACL. Keep these files inside the current user profile and do not share them.

`--state-dir <path>` relocates ownership state, personal `models.d`, `enabled-manifests.json` and personal catalog policy together. Explicit `--catalog-policy` and `--enabled-manifests` still override their corresponding defaults. `CODEX_HOME` controls Codex paths, not bridge state.

## Default workflow

Resolve `<skill-dir>` as the absolute installed Skill/repository directory. PowerShell accepts the same uv entry point; quote paths containing spaces. Preview first, inspect the receipts, then apply:

```powershell
uv run <skill-dir>/scripts/bridge.py audit
uv run <skill-dir>/scripts/bridge.py configure
uv run <skill-dir>/scripts/bridge.py configure --apply
uv run <skill-dir>/scripts/bridge.py sync --config <codex-home>/cli-proxy.config.toml
uv run <skill-dir>/scripts/bridge.py sync --config <codex-home>/cli-proxy.config.toml --apply
uv run <skill-dir>/scripts/bridge.py probe --models <model-id>
```

Here `<codex-home>` is `$env:CODEX_HOME` when set, otherwise `%USERPROFILE%\.codex`; substitute the actual path, not the literal placeholder. Then start Codex with `codex --profile cli-proxy`. Do not rewrite root `model_provider` to `cli_proxy` when ChatGPT history should stay visible in Desktop.

Pass `--proxy-config` and `--proxy-binary` when PATH discovery misses the Windows install.

## Optional Desktop-transparent mode

`configure-desktop` deploys `node transparent_proxy.mjs` as a detached process instead of a LaunchAgent. Changed root writes require preview plus `--expected-sha256 <approved-sha256> --apply`. Unchanged config/runtime and a healthy listener do not trigger another start. The user can also keep a terminal open:

```text
node <skill-dir>/scripts/transparent_proxy.mjs
```

If that process exits, Codex Desktop on `127.0.0.1:8318` fails until it is started again.

## GLM Coding Plan

Do not run `npx @z_ai/coding-helper`. Isolated CLIProxyAPI can host GLM only after a Responses probe passes. Codex Router remains optional and is a Node process, not a Windows service from this Skill.

## What this Skill does not require

- Ruby
- Homebrew
- macOS LaunchAgents
- a second CLIProxyAPI on the same OAuth directory
