import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, writeFile, chmod, rm } from "node:fs/promises";
import http from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { once } from "node:events";
import test from "node:test";

const proxyScript = fileURLToPath(new URL("../scripts/transparent_proxy.mjs", import.meta.url));

async function close(server) {
  await new Promise((resolve) => server.close(resolve));
}

async function fixture(t, installedVersion, floor) {
  const root = await mkdtemp(join(tmpdir(), "codex-proxy-version-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const helper = join(root, "helper.mjs");
  await writeFile(helper, 'process.stdout.write("fixture-key");\n');
  const codex = join(root, "codex-fixture");
  if (installedVersion) {
    await writeFile(codex, `#!${process.execPath}\nprocess.stdout.write(${JSON.stringify(`codex-cli ${installedVersion}`)});\n`);
    await chmod(codex, 0o700);
  }
  const upstream = http.createServer((request, response) => {
    const chunks = [];
    request.on("data", (chunk) => chunks.push(chunk));
    request.on("end", () => {
      response.setHeader("content-type", "application/json");
      response.end(JSON.stringify({ headers: request.headers, body: Buffer.concat(chunks).toString() }));
    });
  });
  upstream.listen(0, "127.0.0.1");
  await once(upstream, "listening");
  t.after(() => close(upstream));
  // Reserve an ephemeral port exactly as the Python HTTP integration fixture does.
  const reservation = http.createServer();
  reservation.listen(0, "127.0.0.1");
  await once(reservation, "listening");
  const port = reservation.address().port;
  await close(reservation);
  const processHandle = spawn(process.execPath, [proxyScript], {
    cwd: root,
    env: {
      ...process.env, HOME: root, CODEX_HOME: join(root, "codex"),
      CODEX_BRIDGE_LISTEN_PORT: String(port),
      CODEX_BRIDGE_UPSTREAM_PORT: String(upstream.address().port),
      CODEX_BRIDGE_HELPER: helper, CODEX_BRIDGE_HELPER_CMD: process.execPath,
      CODEX_BRIDGE_HELPER_ARGS: "[]", CODEX_BRIDGE_CODEX_BIN: codex,
      ...(floor ? { CODEX_BRIDGE_MIN_CLIENT_VERSION: floor } : { CODEX_BRIDGE_MIN_CLIENT_VERSION: "0.160.0" }),
    }, stdio: ["ignore", "pipe", "pipe"],
  });
  t.after(async () => {
    if (processHandle.exitCode !== null) return;
    const stopped = once(processHandle, "exit");
    processHandle.kill("SIGTERM");
    const timer = setTimeout(() => processHandle.kill("SIGKILL"), 3000);
    await stopped;
    clearTimeout(timer);
  });
  let errorText = "";
  processHandle.stderr.on("data", (data) => { errorText += data; });
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("fixture proxy readiness timeout")), 5000);
    processHandle.stdout.on("data", (data) => {
      if (data.toString().includes("listening on")) { clearTimeout(timer); resolve(); }
    });
    processHandle.once("exit", () => { clearTimeout(timer); reject(new Error(errorText || "fixture proxy exited")); });
  });
  return async (version) => {
    const body = JSON.stringify({ model: "fixture-model", service_tier: "priority", input: "fixture" });
    return await new Promise((resolve, reject) => {
      const request = http.request({ host: "127.0.0.1", port, path: "/v1/responses", method: "POST",
        headers: { "content-type": "application/json", authorization: "Bearer fixture-codex", ...(version ? { Version: version } : {}) } }, (response) => {
        const chunks = [];
        response.on("data", (chunk) => chunks.push(chunk));
        response.on("end", () => {
          try {
            assert.equal(response.statusCode, 200);
            const result = JSON.parse(Buffer.concat(chunks));
            assert.equal(result.body, body);
            assert.equal(result.headers.authorization, "Bearer fixture-key");
            resolve(result.headers.version);
          } catch (error) { reject(error); }
        });
      });
      request.on("error", reject);
      request.end(body);
    });
  };
}

test("older and missing Version headers are lifted to installed Codex", async (t) => {
  const request = await fixture(t, "0.161.2");
  assert.equal(await request("0.159.9"), "0.161.2");
  assert.equal(await request(), "0.161.2");
});

test("newer Version header is preserved", async (t) => {
  const request = await fixture(t, "0.161.2");
  assert.equal(await request("0.170.0"), "0.170.0");
});

test("unavailable Codex uses the configured floor", async (t) => {
  const request = await fixture(t, null, "0.162.3");
  assert.equal(await request("0.160.0"), "0.162.3");
  assert.equal(await request("0.170.0"), "0.170.0");
});

test("configured floor also wins over an older installed Codex", async (t) => {
  const request = await fixture(t, "0.150.0", "0.162.3");
  assert.equal(await request("0.160.0"), "0.162.3");
});
