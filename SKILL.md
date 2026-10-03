---
name: codex-cli-model-bridge
description: Configure or troubleshoot Codex custom Providers and proxy model catalogs. Use for local bridge or picker setup and repair, including proxy Fast-mode routing; not for model news, comparisons, or a mere model-name mention.
---

# Codex CLI Model Bridge

Maintain Codex configuration and catalogs for CLIProxyAPI (CPA). Codex selects one Provider for a task; catalog entries cannot route individual models to different Providers. Preserve the Provider owning the dominant indexed history, normally `openai`; never rewrite task rows to match a Provider change.

Desktop coexistence keeps `model_provider = "openai"`, ChatGPT login and a loopback `openai_base_url` pointing to the owner-only header proxy. All catalog models use that same Provider and bridge. Do not make `cli_proxy` or vendor `ZAI` the Desktop default when history belongs to `openai`. The isolated `cli-proxy.config.toml` profile is the Windows default and a fallback elsewhere. Custom Providers need the Responses API; Chat Completions alone is insufficient.

Resolve this loaded Skill directory as `<skill-dir>` and use `uv run <skill-dir>/scripts/bridge.py`. Require uv, Python 3.11+, Codex CLI and authenticated CPA routes; Desktop-transparent mode also needs Node.js.

## Audit and mode selection

Start with `uv run <skill-dir>/scripts/bridge.py audit`. Inspect redacted TOML validity and permissions, Provider/history counts and integrity digest, ChatGPT or command-backed auth, active catalog, live `/v1/models`, missing routes and bridge ownership. Versions identify the installation, not proof of compatibility. Pass `--proxy-config` and the verified listener's `--proxy-binary` when discovery identifies another installation.

| Active path | Action |
| --- | --- |
| Router on `127.0.0.1:4202` | Preserve Router ownership; do not replace its catalog with the CPA bridge. |
| Desktop-transparent bridge on `127.0.0.1:8318` | Preserve `openai`, ChatGPT login and history; repair the smallest failing layer. |
| Isolated profile / Windows | Configure and sync the separate profile; leave root Provider unchanged. |
| Root Provider differs from dominant history | Offer explicit history repair before model work; restore is not a routine installation step. |

Fast settings and validation: [fast-mode.md](references/fast-mode.md).
Spawn transport, model identity and maintenance: [spawn-compatibility.md](references/spawn-compatibility.md).
GLM Coding Plan and existing Router ownership: [glm-coding-plan.md](references/glm-coding-plan.md).
Windows installation and equivalent uv commands: [windows.md](references/windows.md).

## Commands

All writes preview without `--apply`; use verified live route IDs for `<model-id>`. Preserve the default model unless explicitly requested.

| Command after `uv run <skill-dir>/scripts/bridge.py` | Purpose |
| --- | --- |
| `audit` | Read-only configuration, history, route and catalog checks. |
| `configure` | Isolated profile and owner-only credential helper; preserves unrelated profile sections and root config. |
| `sync` | Catalog preview; apply after reviewing the whole diff and live routes. |
| `configure-desktop` | Root Provider/catalog and transparent-runtime preview. |
| `configure-multi-agent` | Preview CPA's `codex.optimize-multi-agent-v2` flag; never restarts CPA. |
| `restore-default` | Explicit history repair: restore dominant Provider/native model and remove root catalog/base-URL overrides. |
| `probe --models <model-id>` | Ephemeral, read-only `codex exec` using the isolated profile. |
| `probe --desktop --models <model-id>` | Probe root catalog without changing Provider identity. |
| `probe --desktop --shell --models <model-id>` | Require a real successful `pwd` event. |
| `probe --desktop --tool-sequence --models <model-id>` | Require ordered successful `pwd` and `git --version` events. |
| `probe-multi-agent --models <model-id>` | Synthetic `agent_message` delivery; not native spawn acceptance. |
| `validate-manifest <path>` | Validate metadata without installing a route. |

The isolated helper reads the existing CPA client key without copying it into Codex config. Python helpers use a stable host interpreter, not the uv/virtual environment running the bridge; retain an existing Ruby `.rb` helper. Use `codex --profile cli-proxy` for the installed profile. Sync's live authentication reads command-backed auth under `[model_providers.cli_proxy]`, not the transparent root's ChatGPT login. If root config lacks that Provider, pass the configured isolated file with `--config <codex-home>/cli-proxy.config.toml`. If the Desktop catalog differs, also pass `--catalog <active-catalog-path>` matching root `model_catalog_json`; never skip live checks to bypass missing auth.

## Preview and apply

Changed root writes (`configure-desktop`, `restore-default`) and CPA multi-agent writes require a preview and its current `config_sha256`:

```sh
uv run <skill-dir>/scripts/bridge.py configure-desktop
uv run <skill-dir>/scripts/bridge.py configure-desktop \
  --expected-sha256 <approved-sha256> --apply
```

Use the same protocol for `restore-default` and `configure-multi-agent`. Approval covers the finding and diff; rerun preview if the hash changes. Root printable diffs show only managed top-level `model`, `model_provider`, `model_catalog_json` and `openai_base_url`, not unrelated configuration. Mutations preserve unrelated TOML and create owner-only backups. History repair refuses a minority Provider unless `--allow-minority-provider` is explicit; when the existing model is not native, its fallback prefers visible native entries, then the smallest `priority` with a stable slug tie-break; only an all-hidden cache falls back to the full directory.

Isolated configuration and catalog sync use their normal preview then `--apply`:

```sh
uv run <skill-dir>/scripts/bridge.py configure
uv run <skill-dir>/scripts/bridge.py configure --apply
uv run <skill-dir>/scripts/bridge.py sync --config <codex-home>/cli-proxy.config.toml
uv run <skill-dir>/scripts/bridge.py sync --config <codex-home>/cli-proxy.config.toml --apply
```

Desktop configuration requires dominant OpenAI history, healthy ChatGPT auth, distinct loopback endpoints, a valid catalog/default model, Node and the helper. Its default listener is `127.0.0.1:8318`, forwarding to authenticated CPA on `127.0.0.1:8317`; it replaces Authorization and lifts missing or old `Version` headers without changing the request body. The version floor is the newer of installed Codex and `CODEX_BRIDGE_MIN_CLIENT_VERSION` (default `0.160.0`); already-newer headers remain unchanged. `CODEX_BRIDGE_CODEX_BIN` selects the CLI used for version detection, otherwise `/opt/homebrew/bin/codex` is used if present, falling back to PATH's `codex` only when that file is absent.

It installs the runtime under the default bridge state path and, on macOS, a LaunchAgent labeled `com.zhijian.codex-cli-model-bridge-transparent-proxy`; Windows/Linux use detached Node. Review `service_action` (`none`, `start`, `restart`) and runtime/plist change flags and hashes before applying; old contents are not printed. Config/runtime and expected plist must all match with a healthy listener to return `unchanged` without writes or starts. Missing or changed runtime/plist needs deployment even if TOML is unchanged; plist updates reload the service definition rather than merely kickstarting old settings. A service action also requires the current config SHA, and any supplied stale SHA is rejected.

CPA config writes and service changes are separate. Identify the listener executable and actual service manager, preserve patches and obtain restart approval before switching that owner. Homebrew installation does not prove ownership. Never run a second CPA against the same OAuth directory.

## Catalog synchronization

Full sync refreshes native metadata from read-only `models_cache.json` and overlays selected manifests. Metadata never selects a Provider. If a native template is missing, prefer visible native entries by smallest `priority` with a stable slug tie-break; use the full cache only when none are visible. The receipt reports `template_fallbacks` for review. Speed capabilities are not inferred from that fallback.

Default state is `~/.config/codex-cli-model-bridge`; `--state-dir` relocates state, personal `models.d`, `enabled-manifests.json` and policy lookup together. Policy precedence is explicit `--catalog-policy`, personal `catalog-policy.json`, then `<skill-dir>/policies/catalog.json`. Policies are complete files, not overlays. Retain exact slugs in `protected_native_model_ids`; manifests cannot `supersede` them. Hidden native IDs remain with `visibility = "hide"`, preserving tasks and routes.

Full sync uses bundled manifests enabled by `{"schema_version": 1, "enabled": ["<model-id>"]}` when that file exists, plus personal manifests. Invalid, duplicate or unknown enabled IDs block sync. Explicit `--models <comma-separated-ids>` selects available manifests without consulting the enabled file; unselected entries and order remain intact except explicit supersedes removals. Subset sync does not refresh other native metadata or reapply its visibility policy, and cannot combine with `--prune-managed`.

Preserve manual entries and report collisions; `--adopt` needs authorization for the exact slug. Previously managed entries with missing manifests remain unless explicitly pruned. Full `--prune-managed` removes stale custom entries but restores native counterparts under the visibility policy. Review entire `changes`, `field_changes` and `order_changed`, not only selected models; onboarding must not alter unrelated entries. See [model-manifests.md](references/model-manifests.md) for metadata and route evidence.

## Verification

Probe affected models through the active mode. Text probes require the exact expected final marker; shell/sequence probes also require real successful command events, not printed simulations. `--desktop` reads the active root catalog; `--catalog` supplies an explicit one-shot override to Codex. `probe --config` accepts only active `$CODEX_HOME/config.toml`; for a named configuration use `--profile <name>` without `--desktop`. Probes do not rewrite config or copy credentials.

Direct HTTP success or a listed ID is not Codex acceptance. Synthetic multi-agent probes report `native_spawn_tested=false`; claim spawn support only after an authorized real child task verifies delivery, tools, result and recorded model/effort. Do not spawn merely because a reference was read. For third-party shell failures caused by inherited `tool_mode = "code_mode_only"`, test `"tool_mode": null` only in the affected manifest; never disable native code mode globally.

Rerun audit and sync after changes; the second sync must be idempotent. See [troubleshooting.md](references/troubleshooting.md) for classification and rollback.

## Safety boundaries

- CPA remote management stays disabled; CPA exposed on LAN must require a client API key. Do not rebind an existing shared CPA listener as part of bridge setup. The transparent header proxy must remain loopback-only; its local CPA upstream does not require changing that server's existing listener.
- Preserve unrelated config, MCP, hooks, skills, permissions, trust and ChatGPT login. Never print keys, bearer headers, OAuth data, codes, helper output or secret-bearing config blocks.
- Keep config, catalogs, state, helpers and backups owner-only (`0600` for private Unix files; user-private locations/ACLs on Windows). Use command-backed auth or the header proxy, not embedded `experimental_bearer_token`.
- Never edit Codex SQLite or app bundles. Do not switch default Provider without history counts and explicit acceptance if dominant history would become hidden.
- Do not restart an active session's CPA/transparent route. Arrange an approved maintenance window and retain rollback paths. Respect upstream account ownership, subscription terms and quotas.

## Completion gate

Apply checks relevant to the requested mode and change:

- Root Provider stays aligned with history; root/history repairs preserve the integrity-checked inventory digest.
- Selected isolated profile or Desktop bridge is valid and healthy; Desktop retains ChatGPT login.
- Catalog has no unapproved collision; protected/hidden native entries remain and all managed/listed/default routes are live.
- Each affected model passes the Codex probe; requested shell, sequence, spawn and Fast checks have their own evidence.
- Repeated sync makes no changes. Report versions, endpoints, mode/Provider, catalog changes, fallback receipts, probe results, backups and approved reload/rollback.

Unverified routes stay unverified; do not advertise them as usable from `/v1/models` alone.
