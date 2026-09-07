# Spawn compatibility and CPA maintenance

Use this reference for third-party subagent failures, context inheritance, model identity, and upgrades affecting those paths. Inspect runtime definitions first; the details below describe the V1/V2 behavior observed with Codex 0.153.4, not a permanent schema contract.

## Version and inheritance

| Intent | V1 | V2 |
| --- | --- | --- |
| No parent conversation | `fork_context=false` or omission | `fork_turns="none"` |
| All available parent history | `fork_context=true` | `fork_turns="all"` or omission |
| Recent N turns | No equivalent parameter | `fork_turns="N"`, positive integer string |

- V2 does not accept V1's `fork_context`. Restored tasks may retain an older multi-agent version; inspect the active schema and recorded version, not just the selected model.
- The recent-turn window can include the current dispatch turn. `"1"` does not necessarily mean the previous completed turn.
- `none` excludes parent conversation, not system/project/role instructions or filesystem access. History inheritance is not workspace isolation.
- Full-history forks inherit parent model and reasoning settings; use a self-contained `none` task for independent cross-model work, subject to the active tool schema.
- A role can override the requested model. Check the child's recorded model/effort, not its self-description or the request alone.
- Tool descriptions may abbreviate the model list. In Codex 0.153.4, five displayed examples were not a five-model allowlist. Use the actual callable schema and model catalog.

## Message-loss diagnosis

Symptoms include HTTP 422 / `ModelInput`, or a child that starts successfully but returns a generic request for instructions.

1. Compare the same bounded task as a normal user message and as V2 `agent_message` input.
2. Verify `codex.optimize-multi-agent-v2` and identify the route's built-in or plugin executor.
3. Inspect whether that executor invokes the V2-aware conversion. Ordinary Responses-to-Chat conversion can omit the task item entirely.
4. Reuse CPA's existing compatibility transform. Do not implement another transform in the 8318 header proxy.

Historical case, 2026-09-07: a WorkBuddy-backed GLM route lost the task in the plugin executor while ordinary chat succeeded. A local fix changed `internal/pluginhost/adapters_executors.go` to reuse `helps.TranslateRequestWithCodexMultiAgentV2` across execution paths. Regression coverage included stream/non-stream delivery, ordering, duplication, compatibility-disabled behavior, and non-Codex callers. Bare upstream v7.2.152 did not contain that fix when inspected. Recheck the target release before applying or retaining a patch; the patch itself is not distributed by this Skill.

## Verification sequence

`probe-multi-agent` sends a synthetic protocol item and checks a random task marker in the completed assistant response. It does not exercise native spawn, role selection, context forking, or parent/child coordination.

When authorized to call models and spawn children:

1. Dispatch a bounded arithmetic task to the affected model with `fork_turns="none"`; prohibit tools and further delegation in the child. Verify the exact answer after completion.
2. Inspect the child record for actual model, reasoning effort and multi-agent version. A returned child ID alone is insufficient.
3. Test inheritance using fresh markers in the parent conversation, without repeating their values in the child task. Check `none`, a specified recent-turn window, and full history only where relevant; use the parent model for full-history forks.
4. Record expected versus observed results. These checks validate task transport and inheritance, not general model quality.

The 2026-09-07 local case passed real GLM and Gemini V2 tasks after the CPA patch. Those dated results are not current acceptance for another installation. Never automatically spawn agents merely because this reference was loaded.

## Restart and upgrade handling

- Identify the listener PID, executable path and controlling service before a change. A PATH or Homebrew version probe may describe a different installed binary; pass the verified executable to `audit --proxy-binary` when needed.
- `configure-multi-agent` writes the approved configuration only. It leaves runtime verification to subsequent checks and never selects a service manager automatically.
- Keep a single listener owner. For custom launchd/systemd/container/manual deployments, reload or restart that same owner after approval rather than starting the Homebrew copy.
- Before upgrading, compare local patches with target source. Retain necessary patches and their regressions; retire them only after equivalent upstream behavior is verified.
- Keep a recoverable executable and configuration backup. Preserve authentication and plugin state; do not overwrite new OAuth or usage state as part of a binary-only rollback.
- After an authorized service change, verify routes and affected native spawn cases. Unit tests or a protocol probe do not establish desktop runtime refresh.

Keep installation-specific paths, account data and restart identifiers in private maintenance notes outside this repository.
