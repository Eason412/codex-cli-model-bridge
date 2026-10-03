# Setup manual for agents

This manual is for AI coding agents (Claude Code, Codex, Gemini CLI and similar) that set up Codex CLI Model Bridge for a user. Follow the steps in order and check each step's success condition before moving on. People should read [README.md](README.md); repair, maintenance and safety rules live in [SKILL.md](SKILL.md) and [references/](references/).

## Ground rules

- Write commands only preview without `--apply`. Show the user the relevant part of each preview before applying.
- Ask the user first before you: install software; change CPA's config; start or restart CPA or the transparent proxy; apply `configure-desktop`; run `probe` (it spends model quota); create a scheduled job; edit `config.toml` by hand.
- Never print, copy or paste API keys, bearer tokens, OAuth data or credential-helper output. Do not read the CPA client key yourself; the bridge reads it through its helper.
- Signing in to upstream Coding Plan or subscription accounts happens in CPA and usually needs a browser. Pause and ask the user to do it.
- When a success condition fails, stop and report the receipt's `error` and related fields. Do not retry with other flags at random; see [references/troubleshooting.md](references/troubleshooting.md).
- `<skill-dir>` is the absolute path of this repository; quote it if it contains spaces. `<codex-home>` is `$CODEX_HOME`, or `~/.codex` when unset. Commands work from any directory.

## 1. Check prerequisites

Run each check and note the result:

| Check | Command | Expected |
| --- | --- | --- |
| uv | `uv --version` | A version. If missing, ask, then install per the [uv docs](https://docs.astral.sh/uv/getting-started/installation/) |
| Codex CLI | `codex --version` | A version. Desktop mode also needs Codex signed in with ChatGPT; `audit` confirms this in step 4 |
| Node.js (macOS desktop mode) | `node --version` | A version. If missing, ask, then install it |
| CPA | `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8317/v1/models` | `401`: CPA is running and requires a client key, which is correct. `000`: nothing is listening, go to step 2. `200`: CPA accepts requests without a key; tell the user and recommend setting `api-keys` |

Choose the mode:

- macOS: desktop transparent proxy (default). Codex keeps `model_provider = "openai"` and its ChatGPT sign-in.
- Windows: separate profile, used with `codex --profile cli-proxy`; follow [references/windows.md](references/windows.md) for paths.
- Linux: separate profile unless the user asks for the transparent proxy.
- If Codex's `openai_base_url` already points to Codex Router (`127.0.0.1:4202`) or another gateway, stop and ask. Do not replace a catalog that another gateway manages; see [SKILL.md](SKILL.md).

## 2. Install CPA (only if step 1 found none)

Ask before installing.

1. Install. macOS: `brew install cliproxyapi`. Windows: the [official release](https://github.com/router-for-me/CLIProxyAPI/releases). Linux and others: the [CPA quick start](https://help.router-for.me/introduction/quick-start).
2. Configure per the [basic configuration guide](https://help.router-for.me/configuration/basic): `host: "127.0.0.1"`, `port: 8317`, remote management disabled, and a client key under `api-keys`. Let the user choose or generate the key, and do not echo it in chat. The Homebrew service reads `$(brew --prefix)/etc/cliproxyapi.conf`.
3. Start it. macOS: `brew services start cliproxyapi`; elsewhere, as installed.
4. Pause and ask the user to sign in to their upstream accounts as the CPA docs describe.

Success: the CPA check from step 1 returns `401`.

## 3. Get the bridge

Clone into a stable location, not a temporary directory. The scheduled sync in step 9 runs from this path.

```sh
git clone https://github.com/Eason412/codex-cli-model-bridge.git
uv run <skill-dir>/scripts/bridge.py --help
```

Success: the help lists `audit`, `configure`, `configure-desktop`, `sync`, `probe` and the other subcommands.

If your agent supports `SKILL.md` skills, the user may link the repository into its skills directory for later maintenance, for example `~/.agents/skills/codex-cli-model-bridge` for Codex. This is optional.

## 4. Audit

```sh
uv run <skill-dir>/scripts/bridge.py audit
```

On a fresh setup, `"status": "attention"` is expected; keep `findings` for the final report. If the CPA config file is not discovered, pass `--proxy-config <path>`. If `history.dominant_provider` is not `openai` on macOS, or the ChatGPT sign-in is missing, stop and ask; desktop mode would hide existing tasks.

## 5. Create the profile and key helper

```sh
uv run <skill-dir>/scripts/bridge.py configure
uv run <skill-dir>/scripts/bridge.py configure --apply
```

This writes `<codex-home>/cli-proxy.config.toml` and an owner-only helper that reads the CPA client key. The root Codex config is not touched.

Success: `"status": "applied"`, or `"unchanged"` if it already existed.

## 6. Build the model catalog

```sh
uv run <skill-dir>/scripts/bridge.py sync --config <codex-home>/cli-proxy.config.toml
```

Check the preview: `"status": "planned"`, `source.live_model_count` greater than 0, and the model IDs under `changes.added`. Show the user that list, then apply:

```sh
uv run <skill-dir>/scripts/bridge.py sync --config <codex-home>/cli-proxy.config.toml --apply
```

Success: `"status": "applied"` (or `"unchanged"`), and `catalog` names the written file. A `"blocked"` result never writes; report `error` together with `codex_error`, `missing_configured_models` or `manifest_errors` when present.

## 7. Connect the transparent proxy (macOS desktop mode only)

```sh
uv run <skill-dir>/scripts/bridge.py configure-desktop
```

Show the user the preview: `model_provider` stays `openai`, `openai_base_url` becomes the local proxy (`http://127.0.0.1:8318/v1`), `model_catalog_json` points to the step 6 catalog, and `model` is unchanged. Also show `service_action` (`none`, `start` or `restart`); a restart briefly interrupts Codex requests in flight. After the user agrees, apply with the preview's checksum:

```sh
uv run <skill-dir>/scripts/bridge.py configure-desktop --expected-sha256 <config_sha256> --apply
```

Success: `"transparent_proxy_healthy": true` and `"status": "applied"` (or `"unchanged"`). If the root config changed, a backup `config.toml.backup-<timestamp>` sits next to it. A checksum mismatch means the config changed after the preview; preview again.

Separate-profile mode skips this step.

## 8. Verify with a real call

Ask first: this spends a little quota on the chosen model. Use one model ID from step 6.

```sh
# desktop mode
uv run <skill-dir>/scripts/bridge.py probe --desktop --shell --models "<model-id>"
# separate-profile mode
uv run <skill-dir>/scripts/bridge.py probe --shell --models "<model-id>"
```

Success: `"ok": true` and `"shell_executed": true`. Then run `audit` again and expect `"status": "ready"`, `catalog.codex_load_error` null and `catalog.in_sync_with_live_list` true.

## 9. Schedule the daily sync (with the user's consent)

New models reach Codex only when `sync` runs. The scheduled command is:

```sh
# desktop mode: reads the root config and its catalog
uv run <skill-dir>/scripts/bridge.py sync --apply
# separate-profile mode
uv run <skill-dir>/scripts/bridge.py sync --config <codex-home>/cli-proxy.config.toml --apply
```

Requirements for the job:

- Run it after CPA (and, in desktop mode, the transparent proxy) is up. If the same job upgrades or restarts CPA, first wait until `curl -sf http://127.0.0.1:8318/v1/models` succeeds (desktop mode) or the step 1 CPA check returns `401`.
- Schedulers start with a minimal PATH. Use absolute paths for `uv`, and make sure `codex` is on PATH or pass `--codex <path>`; without it sync refuses to run.
- Keep stdout in a log; each run prints a JSON receipt.

If the user already has a daily CPA upgrade script, append the command after its restart lines. Otherwise propose a launchd LaunchAgent (macOS) or Task Scheduler entry (Windows), and show it to the user before installing.

Unattended runs are safe: sync writes only a catalog that Codex's own parser (`codex debug models`) loads, and it writes nothing when the live list fails or is empty, when settings are invalid, when a model the config uses would disappear, or when the route belongs to another gateway. Each change keeps one `<catalog>.previous`.

## 10. Personal settings (only when the user asks)

- Hide models or change defaults in `~/.config/codex-cli-model-bridge/overrides.json`, then preview and apply `sync`. Format and rules: [references/model-manifests.md](references/model-manifests.md). Example:

  ```json
  {"schema_version": 1, "models": {"gpt-image-*": {"visibility": "hide"}}}
  ```

- One context length for every model: add `model_context_window = <tokens>` at the top level of `<codex-home>/config.toml` (ask first and back the file up). Codex caps it at each model's `max_context_window`, compacts at 90% of the window, and treats 95% as the usable maximum.

## 11. Finish

Tell the user to restart Codex, both the desktop app and any running CLI sessions; open tasks pick up the new catalog only after a reload. Then report:

- the mode, plus the Codex and CPA versions;
- the catalog path and model count;
- the probe results;
- the backups created (`config.toml.backup-*`, `<catalog>.previous`);
- whether the daily sync is scheduled, and where its log goes;
- anything skipped or still failing, with the receipt's error.
