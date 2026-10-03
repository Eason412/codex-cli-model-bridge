# Spawn compatibility and CPA maintenance

Use this reference for third-party child-task failures, context inheritance, model identity and relevant CPA maintenance. Dispatch parameters come from the actual callable runtime schema; follow the user's `codex-subagents` Skill for delegation policy, model and effort. This reference does not define a second V1/V2 parameter contract.

## Runtime evidence

- Inspect the active schema and recorded multi-agent version, including restored tasks. Tool-description examples are not a complete model allowlist.
- Check the child's recorded model/effort and role overrides, not its self-description or requested values alone.
- Conversation inheritance is not filesystem or memory isolation. Verify inherited content from the actual child request when relevant.
- 近 N 轮可能包含当前派发轮；完整历史 fork 会继承父模型和推理强度。
- A task may require transport compatibility, tool compatibility and native spawn acceptance separately; one success does not prove the others.

## Message-loss diagnosis

Symptoms include HTTP 422 / `ModelInput`, or a child that starts but asks for instructions.

1. Compare the same bounded task as an ordinary user message and as `agent_message` input.
2. Verify `codex.optimize-multi-agent-v2` and identify the route's built-in or plugin executor.
3. Inspect whether that executor invokes CPA's compatibility conversion. An ordinary Responses-to-Chat conversion can omit the task item.
4. Reuse CPA's compatibility transform; do not duplicate it in the 8318 header proxy. Versions alone are not acceptance.

Preview and enable only the canonical CPA config flag:

```sh
uv run <skill-dir>/scripts/bridge.py configure-multi-agent
uv run <skill-dir>/scripts/bridge.py configure-multi-agent \
  --expected-sha256 <approved-sha256> --apply
uv run <skill-dir>/scripts/bridge.py probe-multi-agent --models <model-id>
uv run <skill-dir>/scripts/bridge.py probe --desktop --tool-sequence --models <model-id>
```

Changed writes require the preview's current SHA-256 and create an owner-only backup. A supplied stale hash is rejected; an unchanged write needs no hash. This command never restarts CPA or claims runtime verification. The conversion is Codex-client/config gated; plugin executors must invoke it too.

## Optional legacy Kimi repair

Only an old customized CPA v7.3.3 deployment with the demonstrated Kimi `agent_message` gap should consult [kimi-legacy-compatibility.md](kimi-legacy-compatibility.md). Ordinary onboarding does not require this patch or reference; inspect current behavior before retaining any old source patch.

## Antigravity content-related 429

CPA maintainer guidance: [issue #5848](https://github.com/router-for-me/CLIProxyAPI/issues/5848#issuecomment-5681658621), with a related [subagent report #5695](https://github.com/router-for-me/CLIProxyAPI/issues/5695). The maintainer describes some system-text rejections as masked `429 RESOURCE_EXHAUSTED`, not actual quota exhaustion. This is not an explanation for every 429; distinguish explicit quota/reset information, cooldown, network errors and region-related 400 responses.

1. Verify the actual CPA executable and current upstream error. Compare a minimal request and the failing request on the same route and reasoning effort. If account pinning is unavailable, record that round-robin remains a confounder rather than claiming a fixed-account experiment.
2. Reduce a private copy of the failing request to isolate the triggering system-text phrase. Preserve the original for the final replay. Removing instructions or tools is a diagnostic control, not a production workaround. Keep prompts, credentials and raw logs out of public reports.
3. Check system text originating from persistent memory as well as base instructions. In a September 2026 reproduction, a memory summary quoted an identity sentence that an existing prefix-only correction did not cover. Changing only that quotation made the otherwise unchanged request complete.
4. When the phrase is confirmed and configuration changes are authorized, back up CPA configuration and merge the phrase into the existing `antigravity.sensitive-words` list. Do not duplicate the provider block or replace existing entries:

   ```yaml
   antigravity:
     sensitive-words:
       - "<exact phrase isolated by the controlled test>"
   ```

   Replace the placeholder with the demonstrated phrase; do not install a broad guessed keyword list. CPA's built-in mechanism inserts zero-width characters into matched Antigravity system-instruction text. It does not edit local memory files, but it does change outgoing text. Check tasks needing exact quotations accordingly. This Skill documents the mechanism; `bridge.py` does not automatically configure it, and the transparent proxy must not grow a second implementation.
5. Verify hot reload where supported; do not restart a healthy service merely to test this configuration. Replay the original full request, then run a real spawn with the normal role and memory injection retained. Verify model/effort, actual tool output and final response. A 200 or a started child alone is insufficient. Roll back only the newly added entry if it does not help; retain real quota/cooldown controls.

Memory isolation is a separate feature. `fork_turns="none"` does not promise absence of memory. A tested custom role with `memories.use_memories=false` still received a memory summary on one V2 runtime; do not generalize this to all versions or claim isolation from configuration alone. Inspect the actual child request. Cached desktop role lists may also reject a newly added role, independently of memory behavior.

Sanitized acceptance, 2026-09-16: on a locally patched CPA v7.3.3 deployment, adding one confirmed phrase through the existing configuration made the original failing request complete. Four real `gemini-3.8-flash / high / v2` worker spawns, with memory still injected, executed `pwd` and returned the requested markers. This establishes that deployment's tested path, not a universal fix, upstream capacity guarantee, or validation of an unpatched binary.

## Verification sequence

`probe-multi-agent` sends a synthetic protocol item and checks a random task marker in the completed assistant response. It does not exercise native spawn, role selection, context forking, or parent/child coordination.

When authorized to call models and spawn children:

1. Dispatch a bounded arithmetic task using the active schema's no-conversation setting and the user's delegation Skill; prohibit tools and further delegation. Verify the exact answer after completion.
2. Inspect the child record for actual model, reasoning effort and multi-agent version. A returned child ID alone is insufficient.
3. Test the requested inheritance mode with fresh markers in the parent conversation without repeating their values in the child task. Use only modes supported by the actual schema and preserve its model/effort constraints.
4. Record expected versus observed results. These checks validate task transport and inheritance, not general model quality.

Never automatically spawn agents merely because this reference was loaded.

## Restart and upgrade handling

- Identify the listener PID, executable path and controlling service before a change. A PATH or Homebrew version probe may describe a different installed binary; pass the verified executable to `audit --proxy-binary` when needed.
- `configure-multi-agent` writes the approved configuration only. It leaves runtime verification to subsequent checks and never selects a service manager automatically.
- Keep a single listener owner. For custom launchd/systemd/container/manual deployments, reload or restart that same owner after approval rather than starting the Homebrew copy.
- Before upgrading, compare local patches with target source. Retain necessary patches and their regressions; retire them only after equivalent upstream behavior is verified.
- Keep a recoverable executable and configuration backup. Preserve authentication and plugin state; do not overwrite new OAuth or usage state as part of a binary-only rollback.
- After an authorized service change, verify routes and affected native spawn cases. Unit tests or a protocol probe do not establish desktop runtime refresh.

Keep installation-specific paths, account data and restart identifiers in private maintenance notes outside this repository.
