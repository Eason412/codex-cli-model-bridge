"""Verify service-tier passthrough against an isolated local HTTP upstream."""
import contextlib
import http.server
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request


@unittest.skipUnless(shutil.which("node"), "Node.js is required for proxy integration")
class FastProxyTests(unittest.TestCase):
    def test_proxy_preserves_tier_and_model_while_replacing_auth(self):
        received = []

        class Upstream(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                received.append((body, self.headers.get("Authorization")))
                response = json.dumps({"status": "completed", "service_tier": "priority"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

        upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        thread.start()
        def stop_proxy(process):
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)

        try:
            with contextlib.ExitStack() as cleanup:
                raw = cleanup.enter_context(tempfile.TemporaryDirectory())
                helper = Path(raw) / "fixture_auth.py"
                helper.write_text('print("fixture-client-key")\n', encoding="utf-8")
                with socket.socket() as candidate:
                    candidate.bind(("127.0.0.1", 0))
                    port = candidate.getsockname()[1]
                env = {**os.environ, "CODEX_BRIDGE_LISTEN_PORT": str(port),
                       "CODEX_BRIDGE_UPSTREAM_PORT": str(upstream.server_port),
                       "CODEX_BRIDGE_HELPER": str(helper), "CODEX_BRIDGE_HELPER_CMD": sys.executable,
                       "CODEX_BRIDGE_HELPER_ARGS": "[]"}
                process = subprocess.Popen(
                    [shutil.which("node"), str(Path(__file__).resolve().parents[1] / "scripts/transparent_proxy.mjs")],
                    env=env, cwd=raw, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                # 先结束子进程，再清理其工作目录，兼容 Windows 文件占用规则。
                cleanup.callback(stop_proxy, process)
                base = f"http://127.0.0.1:{port}"
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                for _ in range(60):
                    if process.poll() is not None:
                        self.fail("test proxy exited before readiness")
                    try:
                        with opener.open(base + "/__codex_bridge_health", timeout=0.5):
                            break
                    except OSError:
                        time.sleep(0.05)
                else:
                    self.fail("test proxy did not become ready")
                for tier in ("fast", "priority"):
                    payload = json.dumps({"model": "gpt-6-astra", "service_tier": tier, "input": "fixture"}).encode()
                    request = urllib.request.Request(base + "/v1/responses", data=payload,
                                                     headers={"Authorization": "Bearer fixture-codex", "Content-Type": "application/json"})
                    with opener.open(request, timeout=5) as response:
                        self.assertEqual(json.load(response)["service_tier"], "priority")
                    self.assertEqual(received[-1], (payload, "Bearer fixture-client-key"))
        finally:
            upstream.shutdown()
            upstream.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
