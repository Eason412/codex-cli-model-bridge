# Codex CLI Model Bridge

[中文](README.md) | English

**Use your Coding Plan and subscription models directly in Codex.** This tool connects Codex to CLIProxyAPI (CPA) running on your machine, so models such as Kimi, Gemini and DeepSeek appear in the same model picker as GPT, while your ChatGPT sign-in, account features and task history stay intact. New models show up on their own: no code changes, no waiting for a new release.

A Coding Plan is a subscription that vendors such as Moonshot (Kimi) and Zhipu offer for coding tools. CPA is an open-source proxy that runs locally, signs in to those accounts and forwards requests to them.

Current development version: V0.0.5 (unreleased) · [Changelog](changelogs/V0.0.5.md) (Chinese); latest release: [V0.0.4](https://github.com/Eason412/codex-cli-model-bridge/releases/tag/V0.0.4). Derived from [codex-cli-model-bridge in Zhijian Skills](https://github.com/zjp1997720/zhijian-skills/tree/main/skills/codex-cli-model-bridge).

> **Prerequisite: CPA.** This tool connects Codex to CPA; it does not replace it. If you do not have CPA yet, the setup manual installs it first.

## Why use it

- **New models arrive automatically.** The model list comes straight from CPA. Once a daily sync is scheduled, a model that appears in CPA today is in the Codex model picker tomorrow: no code to change, no config to write, no release to wait for.
- **Keep your sign-in and your history.** Many setups switch Codex to a third-party model provider, which makes your earlier tasks disappear from the task list. On macOS this tool keeps the provider as OpenAI and only points the request URL at a local proxy, so Codex stays signed in with ChatGPT and your account features and task history remain.
- **Customize with one line.** Hide a model or change its default reasoning effort with a single line in a small file, wildcards like `gpt-image-*` included. Anything you leave out keeps the defaults CPA provides.
- **Every change is checked and reversible.** A new model catalog is written only after Codex itself has loaded it successfully. Changes to the main Codex config are shown to you first and written only after you confirm. Both the main config and the catalog are backed up before they change, so you can restore them at any time. The CPA key is never written into Codex config and never printed.
- **Connected means actually working.** Verification has the model actually run a command; merely appearing in the model list does not count.

## How it works

```text
Codex ──► local transparent proxy ──► CPA ──► Coding Plan / subscription services
          127.0.0.1:8318               127.0.0.1:8317
```

- **Transparent proxy**: a small local service installed by this tool. Codex sends requests to it; it swaps in the CPA key and forwards them to CPA unchanged.
- **CPA**: routes each request to the right account and provider by model name.
- **Model catalog**: the JSON file the Codex model picker reads. This tool builds it from CPA's current model list, including context length, reasoning levels and other parameters, then applies your personal settings.

Everyday requests pass only through the transparent proxy and CPA; this tool's command line runs only for setup, sync and checks. macOS uses the transparent proxy shown above by default, and it works in both the desktop app and the CLI. Windows defaults to a separate Codex config file used with `codex --profile cli-proxy`, leaving the main config untouched.

## Setting it up

Let your AI agent do it. [SETUP.md](SETUP.md) in this repository is a setup manual written for agents: Claude Code, Codex, Gemini CLI and others can follow it step by step, and it tells them to stop and ask you before installing software, changing configuration, restarting services or spending model quota. Send your agent this:

> Clone https://github.com/Eason412/codex-cli-model-bridge, read its SETUP.md, and follow it to install and configure everything for me. Ask me before installing software, changing configuration, restarting services or spending model quota.

In outline, the agent confirms CPA is running (installing it first if needed), builds the model catalog from CPA's model list, connects Codex to CPA through the local transparent proxy, has one model actually run a command as the acceptance test, and, if you want, schedules a daily sync. Restart Codex afterwards and the new models appear in the model picker.

Only two things need you personally: signing in to your Coding Plan or subscription accounts in CPA (usually in a browser), and, on macOS, keeping Codex signed in with your ChatGPT account. The agent checks for tools such as uv and Node.js and installs missing ones with your consent.

To adjust things later, just tell your agent. Hiding a model or changing its default reasoning effort goes in a personal settings file; giving every model the same context length, such as 500K, takes one line in the Codex config (Codex compacts automatically at 90% of the context window). See [Overrides and extra models](references/model-manifests.md).

## Contributing

Compatibility fixes, improvements and documentation updates are welcome. Before opening a PR:

- Keep each PR to one problem, without personal configuration or unrelated formatting changes.
- Describe the problem with a minimal reproduction, the cause and your approach, and the checks you actually ran; say why any check was skipped instead of marking it as passed.
- Run tests under a temporary HOME and CODEX_HOME. Tests must not read or write your real configuration, or start or stop a real CPA or proxy.
- For model-specific changes, include the model ID, where its metadata came from, the CPA upstream type, and a verification result with sensitive data removed.
- Check diffs, logs and screenshots for keys, OAuth data, private paths and internal addresses before submitting.
- The repository ships no model IDs; personal preferences stay outside it.

The full requirements are in the [contributing guide](CONTRIBUTING.md) (Chinese); the PR template is filled in automatically.

## License

[MIT](LICENSE); the original copyright notice is retained.
