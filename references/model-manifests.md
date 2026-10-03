# Private model manifests

Bundled manifests live in `models/*.json`. Personal manifests live in `<state-dir>/models.d/` (default `~/.config/codex-cli-model-bridge/models.d/`). They are metadata overlays applied to a current native Codex model template; use `--state-dir` consistently when previewing and applying.

Required fields:

- `schema_version`: currently `1`
- `slug`: exact model ID sent to CLIProxyAPI
- `display_name`
- `description`
- `template_slug`: native Codex catalog model whose agent/runtime compatibility fields are inherited
- `context_window` and `effective_context_window_percent`
- `default_reasoning_level` and `reasoning_efforts`
- `input_modalities`
- `priority`

Optional fields:

- `supports_search_tool`: default `false`
- `supports_image_detail_original`: default inherited
- `additional_speed_tiers` and `service_tiers`: when omitted, inherit from the exact matching model in the native cache; when no exact native model exists, default to empty arrays. Explicit values, including empty arrays, override this default. Never inherit another model's speed capabilities through `template_slug`. Non-native overrides require verified route tier semantics; see [fast-mode.md](fast-mode.md).
- `supersedes`: previously managed route IDs that this verified route replaces in the generated catalog. It must not contain a native ID listed in the catalog policy's `protected_native_model_ids`; Codex App Thread creation addresses those exact IDs even when a cosmetic alias exists. A private manifest must never supersede those native IDs.

Do not copy marketing claims blindly. Resolve context and reasoning controls from an exact Provider catalog or observed route, and then confirm the route using `codex exec`.

Codex requires many internal model-catalog compatibility fields. The bridge inherits those fields from a current native template so the managed models stay aligned after Codex updates. The manifest controls only the fields that are specific to the external route.

When `template_slug` is absent from the native cache, sync prefers entries with `visibility = "list"` and the smallest `priority` (highest catalog rank), using slug order to break ties. Only when none are visible does it select from the full native cache. The receipt reports `template_fallbacks` with the requested and actual template. Inspect that receipt rather than treating a historical GPT slug as mandatory. The fallback does not grant Fast; exact-model speed semantics above still apply.

Before adding a manifest:

1. Confirm the route is Responses-compatible.
2. Confirm it appears in CLIProxyAPI `/v1/models`.
3. Choose the closest current native Codex template.
4. Preview `sync` and inspect collisions.
5. Apply, run `probe`, and repeat `sync` for idempotency.

Use the deterministic bridge entry point:

```sh
uv run <skill-dir>/scripts/bridge.py validate-manifest <path>
uv run <skill-dir>/scripts/bridge.py sync --models <model-id>
```

Sync requires command-backed auth in `[model_providers.cli_proxy]`; if root config lacks it, pass the configured isolated file with `--config <codex-home>/cli-proxy.config.toml`, including for a Desktop-transparent catalog. Use `--catalog <active-catalog-path>` when the target differs from the isolated default, and retain live route checks. Apply only after reviewing the complete catalog receipt, then probe through the active mode. A manifest and `/v1/models` listing are not sufficient proof of Codex compatibility.
