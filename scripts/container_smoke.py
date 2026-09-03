"""Run inside the validation image. Token is generated here, never printed."""
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
import tempfile
from pathlib import Path



def main():
    token = secrets.token_hex(32)
    temporary = tempfile.TemporaryDirectory(prefix="quota-smoke-")
    ledger = Path(temporary.name) / "quota.sqlite3"
    # Run as a module from /app: this helper also runs as scripts/container_smoke.py,
    # whose sys.path otherwise contains only /app/scripts, not the service modules.
    subprocess.run([sys.executable, "-m", "deep_quota", "init", str(ledger)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "deep_service:create_app", "--factory", "--host", "127.0.0.1", "--port", "8000", "--workers", "1", "--no-access-log"],
        env={**os.environ, "ARCHAEOLOGIST_SERVICE_TOKEN": token, "ARCHAEOLOGIST_QUOTA_PATH": str(ledger)},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    def request(path, payload=None, authenticated=False):
        headers = {"Content-Type": "application/json"}
        if authenticated:
            headers["Authorization"] = f"Bearer {token}"
            headers["X-Archaeologist-Client-Key"] = "b" * 64
        req = urllib.request.Request("http://127.0.0.1:8000" + path, headers=headers,
                                     data=json.dumps(payload).encode() if payload is not None else None)
        try:
            with urllib.request.urlopen(req, timeout=70) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)
    try:
        for _ in range(50):
            if server.poll() is not None:
                raise RuntimeError("Service exited before startup.")
            try:
                if request("/health")[0] == 200:
                    break
            except urllib.error.URLError:
                pass
            time.sleep(0.1)
        else:
            raise RuntimeError("Service did not become healthy.")
        assert request("/api/analyze/quota-v1", {"repositoryUrl": "https://github.com/pallets/itsdangerous"})[0] == 401
        assert request("/api/analyze/quota-v1", {"repositoryUrl": "https://example.com/a/b"}, True)[0] == 400
        status, graph = request("/api/analyze/quota-v1", {"repositoryUrl": "https://github.com/pallets/itsdangerous"}, True)
        if status != 200:
            raise RuntimeError(f"Live analysis returned HTTP {status}: {graph.get('detail', 'unknown failure')}")
        assert graph["analysis"]["tier"] == "deep"
        assert len(graph["snapshot"]["commit_sha"]) == 40
        assert any(node["kind"] != "file" for node in graph["nodes"])
        assert "repo_root" not in graph
        assert request("/health")[0] == 200
        print(json.dumps({"result": "PASS", "repository": "pallets/itsdangerous", "tier": "deep",
                          "nodes": len(graph["nodes"]), "edges": len(graph["edges"]),
                          "authorization": "verified", "invalid_url": "rejected"}))
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()
        temporary.cleanup()


if __name__ == "__main__":
    main()
