# Private model manifests

Bundled manifests live in `models/*.json`. They are metadata overlays applied to a current native Codex model template.

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

Before adding a manifest:

1. Confirm the route is Responses-compatible.
2. Confirm it appears in CLIProxyAPI `/v1/models`.
3. Choose the closest current native Codex template.
4. Preview `sync` and inspect collisions.
5. Apply, run `probe`, and repeat `sync` for idempotency.
