import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readFile, writeFile, chmod, rm } from "node:fs/promises";
import http from "node:http";
import net from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { once } from "node:events";
import test from "node:test";

const proxyScript = fileURLToPath(new URL("../scripts/transparent_proxy.mjs", import.meta.url));

async function close(server) {
  await new Promise((resolve) => server.close(resolve));
}

async function fixture(t, installedVersion, floor, { codexFromPath = false } = {}) {
  const root = await mkdtemp(join(tmpdir(), "codex-proxy-version-"));
  const sockets = new Set();
  let upstream;
  let reservation;
  let processHandle;
  t.after(async () => {
    try {
      for (const socket of sockets) socket.destroy();
      if (processHandle && processHandle.exitCode === null && processHandle.signalCode === null) {
        const stopped = once(processHandle, "exit");
        processHandle.kill("SIGTERM");
        const timer = setTimeout(() => processHandle.kill("SIGKILL"), 3000);
        try { await stopped; } finally { clearTimeout(timer); }
      }
      if (reservation?.listening) await close(reservation);
      if (upstream?.listening) await close(upstream);
    } finally {
      await rm(root, { recursive: true, force: true });
    }
  });
  const trackSocket = (socket) => {
    sockets.add(socket);
    socket.once("close", () => sockets.delete(socket));
    return socket;
  };
  const helper = join(root, "helper.mjs");
  await writeFile(helper, 'process.stdout.write("fixture-key");\n');
  const codex = join(root, codexFromPath ? "codex" : "codex-fixture");
  if (installedVersion) {
    await writeFile(codex, `#!${process.execPath}\nprocess.stdout.write(${JSON.stringify(`codex-cli ${installedVersion}`)});\n`);
    await chmod(codex, 0o700);
  }
  upstream = http.createServer((request, response) => {
    const chunks = [];
    request.on("data", (chunk) => chunks.push(chunk));
    request.on("end", () => {
      response.setHeader("content-type", "application/json");
      response.end(JSON.stringify({ headers: request.headers, body: Buffer.concat(chunks).toString() }));
    });
  });
  upstream.on("connection", trackSocket);
  upstream.on("upgrade", (request, socket, head) => {
    socket.write([
      "HTTP/1.1 101 Switching Protocols", "Connection: Upgrade", "Upgrade: websocket",
      "Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=",
      `X-Fixture-Version: ${request.headers.version}`,
      `X-Fixture-Authorization: ${request.headers.authorization}`, "", "",
    ].join("\r\n"));
    if (head.length) socket.write(head);
    socket.on("data", (chunk) => socket.write(chunk));
    socket.on("error", () => socket.destroy());
  });
  upstream.listen(0, "127.0.0.1");
  await once(upstream, "listening");
  // Reserve an ephemeral port exactly as the Python HTTP integration fixture does.
  reservation = http.createServer();
  reservation.listen(0, "127.0.0.1");
  await once(reservation, "listening");
  const port = reservation.address().port;
  await close(reservation);
  let script = proxyScript;
  if (codexFromPath) {
    // Replace only the default binary literal in a temporary copy: this exercises
    // the real existsSync/PATH branch without consulting the host's Codex install
    // or introducing a new production configuration option.
    const source = await readFile(proxyScript, "utf8");
    const literal = '"/opt/homebrew/bin/codex"';
    assert.equal(source.split(literal).length, 2);
    script = join(root, "transparent_proxy.mjs");
    await writeFile(script, source.replace(literal, JSON.stringify(join(root, "absent-default-codex"))));
  }
  const env = {
    ...process.env, HOME: root, CODEX_HOME: join(root, "codex-home"), PATH: root,
    CODEX_BRIDGE_LISTEN_PORT: String(port),
    CODEX_BRIDGE_UPSTREAM_PORT: String(upstream.address().port),
    CODEX_BRIDGE_HELPER: helper, CODEX_BRIDGE_HELPER_CMD: process.execPath,
    CODEX_BRIDGE_HELPER_ARGS: "[]",
  };
  // Delete inherited settings so omission tests really exercise production defaults.
  delete env.CODEX_BRIDGE_MIN_CLIENT_VERSION;
  delete env.CODEX_BRIDGE_CODEX_BIN;
  if (floor !== undefined) env.CODEX_BRIDGE_MIN_CLIENT_VERSION = floor;
  if (!codexFromPath) env.CODEX_BRIDGE_CODEX_BIN = codex;
  processHandle = spawn(process.execPath, [script], {
    cwd: root,
    env, stdio: ["ignore", "pipe", "pipe"],
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
  const sendRequest = async (version) => {
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
      request.on("socket", trackSocket);
      request.setTimeout(5000, () => request.destroy(new Error("fixture HTTP request timeout")));
      request.on("error", reject);
      request.end(body);
    });
  };
  sendRequest.upgrade = async (version) => {
    // Two masked binary WebSocket frames include non-text bytes. Send the first
    // with the HTTP headers to cover upgrade head, then another after the 101.
    const initialFrame = Buffer.from([0x82, 0x84, 1, 2, 3, 4, 0x01, 0x7d, 0x83, 0xfb]);
    const laterFrame = Buffer.from([0x82, 0x83, 5, 6, 7, 8, 0xfa, 0x06, 0x86]);
    const expectedBytes = Buffer.concat([initialFrame, laterFrame]);
    return await new Promise((resolve, reject) => {
      const socket = trackSocket(net.connect(port, "127.0.0.1"));
      let received = Buffer.alloc(0);
      let responseHeaders;
      const timer = setTimeout(() => finish(new Error("fixture upgrade timeout")), 5000);
      const finish = (error) => {
        clearTimeout(timer);
        socket.destroy();
        if (error) reject(error); else resolve(responseHeaders);
      };
      socket.on("error", finish);
      socket.once("end", () => finish(new Error("fixture upgrade ended before echo")));
      socket.once("connect", () => {
        const headers = [
          "GET /v1/responses HTTP/1.1", `Host: 127.0.0.1:${port}`,
          "Connection: Upgrade", "Upgrade: websocket", "Sec-WebSocket-Version: 13",
          "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==", "Authorization: Bearer fixture-codex",
          ...(version ? [`Version: ${version}`] : []), "", "",
        ].join("\r\n");
        socket.write(Buffer.concat([Buffer.from(headers), initialFrame]));
      });
      socket.on("data", (chunk) => {
        try {
          received = Buffer.concat([received, chunk]);
          if (!responseHeaders) {
            const boundary = received.indexOf("\r\n\r\n");
            if (boundary < 0) return;
            const [status, ...headers] = received.subarray(0, boundary).toString().split("\r\n");
            assert.equal(status, "HTTP/1.1 101 Switching Protocols");
            responseHeaders = Object.fromEntries(headers.map((line) => {
              const colon = line.indexOf(":");
              return [line.slice(0, colon).toLowerCase(), line.slice(colon + 1).trim()];
            }));
            assert.equal(responseHeaders["x-fixture-authorization"], "Bearer fixture-key");
            assert.equal(responseHeaders.upgrade, "websocket");
            received = received.subarray(boundary + 4);
            socket.write(laterFrame);
          }
          if (received.length >= expectedBytes.length) {
            assert.deepEqual(received, expectedBytes);
            finish();
          }
        } catch (error) { finish(error); }
      });
    });
  };
  return sendRequest;
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

test("unavailable Codex uses the default floor when the floor environment variable is absent", async (t) => {
  const request = await fixture(t, null);
  assert.equal(await request("0.159.9"), "0.160.0");
  assert.equal(await request(), "0.160.0");
  assert.equal(await request("0.170.0"), "0.170.0");
});

test("missing default Codex binary falls back to PATH without a binary override", async (t) => {
  const request = await fixture(t, "0.163.4", undefined, { codexFromPath: true });
  assert.equal(await request("0.159.9"), "0.163.4");
  assert.equal(await request(), "0.163.4");
});

test("WebSocket upgrade lifts older versions, preserves newer versions, and forwards raw bytes", async (t) => {
  const request = await fixture(t, "0.161.2");
  assert.equal((await request.upgrade("0.159.9"))["x-fixture-version"], "0.161.2");
  assert.equal((await request.upgrade("0.170.0"))["x-fixture-version"], "0.170.0");
});
