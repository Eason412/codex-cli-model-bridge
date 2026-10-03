# Legacy Kimi Responses compatibility

This optional historical procedure applies only to old customized CPA v7.3.3 deployments exhibiting the specific Kimi `agent_message` gap. It is not part of ordinary model onboarding, a current upstream version requirement, or proof that a newer CPA needs the patch. Keep the shipped patch for reproducibility; check the actual executor and existing local changes before using it.

## Historical gap

On CPA v7.3.3, Kimi's `executeResponses` and `executeResponsesStream` copy the Responses payload directly, bypassing the existing Codex V2 input conversion. A controlled reproduction succeeded with system/memory context and no delegation item, failed with an `agent_message` alone, and completed after converting only the delegation item to a standard user message while retaining task text. Ordinary high-effort requests and a namespace-tool probe also completed; these observations do not implicate tool mode or account quota.

Related upstream history: [issue #4801](https://github.com/router-for-me/CLIProxyAPI/issues/4801) describes the same third-party Responses dialect gap. Its closed status does not establish coverage of the Kimi executor.

The optional [CPA v7.3.3 patch](../patches/cpa-v7.3.3-kimi-agent-message.patch) adds client/config-gated `RewriteCodexMultiAgentV2Input` calls to both Kimi Responses entry points and executable regression tests. It does not alter native OpenAI/Codex routes or add another transformation to the transparent proxy. `codex.optimize-multi-agent-v2` must be enabled and the caller must satisfy CPA's Codex-client detection.

Apply only in an authorized CPA source checkout after checking its version and existing changes:

```sh
git apply --check /path/to/skill/patches/cpa-v7.3.3-kimi-agent-message.patch
git apply /path/to/skill/patches/cpa-v7.3.3-kimi-agent-message.patch
go test ./internal/runtime/executor -run 'TestKimiResponsesAgentMessageCompatibility|TestKimiExecutorResponses' -count=1
go build -o /path/to/new-versioned-binary ./cmd/server
```

The patch is not automatically installed by `bridge.py`. Review compatibility again on other CPA versions. Preserve local patches and the old binary; service switching needs a maintenance window and a rollback plan. After deployment, verify a real K3 spawn, its runtime model, task delivery, tool execution and final response. Do not report source tests as production acceptance.

Historical validation recorded with the patch: focused executor tests and server build passed; the original-context HTTP normalization control completed. That historical record did not include patched production deployment or native spawn acceptance. The pre-fix native spawn returned 400.
