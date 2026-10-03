# Overrides and extra models

The catalog is CPA's live Codex-format list. Two personal files in `<state-dir>` (default `~/.config/codex-cli-model-bridge`) adjust it; use the same `--state-dir` when previewing and applying.

## overrides.json

Changes fields of models that are already in the live list:

```json
{
  "schema_version": 1,
  "models": {
    "gpt-image-*": {"visibility": "hide"},
    "kimi-k3": {"tool_mode": null},
    "gpt-6-sol": {"default_reasoning_level": "medium"}
  }
}
```

- Keys are exact model IDs or globs (`*`, `?`, `[...]`). Globs apply in file order, then the exact ID, so an exact key wins.
- Each value replaces top-level catalog fields; `null` removes the field. `slug` cannot change.
- Checked fields: `visibility` is `list` or `hide`; `context_window`, `max_context_window`, `auto_compact_token_limit`, `effective_context_window_percent` (at most 100) and `priority` are positive integers; `default_reasoning_level` must be one of the model's `supported_reasoning_levels`.
- Keys that match nothing are listed in `overrides_unmatched` so retired IDs can be removed later; they do not block sync.

Prefer Codex settings over catalog edits where they exist: `model_context_window` sets one context size for every model, clamped by each model's `max_context_window`, and `model_reasoning_effort` sets the default effort.

## models.d

Adds a route that CPA's `/v1/models` lists but its Codex-format list lacks. One JSON file per model:

- Required: `schema_version` (`1`), `slug` (exact CPA model ID), `display_name`, `description`, `context_window`, `effective_context_window_percent`, `default_reasoning_level`, `reasoning_efforts`, `input_modalities`, `priority`.
- Optional: `template_slug`, a live model whose Codex compatibility fields are copied. When absent or no longer listed, sync uses the visible live model with the smallest `priority` and reports a requested-but-missing template in `template_fallbacks`.
- Optional: `supports_search_tool` (default `false`), `supports_image_detail_original`, `tool_mode` (`null` removes an inherited `code_mode_only`), `additional_speed_tiers` and `service_tiers`. Speed tiers default to empty and are never copied from the template; declare them only after verifying the route's tier semantics, see [fast-mode.md](fast-mode.md).
- `supersedes` is rejected: retired IDs leave the catalog when they leave the live list.

A manifest whose ID appears in the live list is ignored and reported under `manifests_shadowed_by_live_list`; move any still-wanted difference into `overrides.json` and delete the manifest.

Resolve context and reasoning controls from the provider's exact catalog or an observed route, not marketing claims. Before adding a manifest:

1. Confirm the route is Responses-compatible and listed in CPA `/v1/models`.
2. Validate it: `uv run <skill-dir>/scripts/bridge.py validate-manifest <path>`.
3. Preview `sync`, review the receipt, then apply.
4. Run `probe` for the model and repeat `sync` to confirm it is unchanged.

A manifest and a `/v1/models` listing are not proof of Codex compatibility.
