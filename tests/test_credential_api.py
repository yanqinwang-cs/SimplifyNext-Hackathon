import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

from investigator.http_api import create_server
from investigator.llm.bedrock import clear_credential_override


def _request(base: str, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(base + path, data=body, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_debug_credential_api_never_returns_secrets(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SIMPLIFYNEXT_DEBUG_CREDENTIALS", "1")
    clear_credential_override()
    server = create_server(tmp_path / "cases", port=0, run_callback=lambda *_: None)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    secrets = ("TEST_ACCESS_SECRET_123", "TEST_SECRET_SECRET_456", "TEST_TOKEN_SECRET_789")
    try:
        status, payload = _request(base, "POST", "/api/debug/aws-credentials", {"aws_access_key_id": secrets[0], "aws_secret_access_key": secrets[1], "aws_session_token": secrets[2]})
        assert status == 200
        assert all(secret not in json.dumps(payload) for secret in secrets)
        status, payload = _request(base, "GET", "/api/debug/aws-credentials/status")
        assert status == 200 and payload["override_active"] is True
        assert all(secret not in json.dumps(payload) for secret in secrets)
        status, payload = _request(base, "DELETE", "/api/debug/aws-credentials")
        assert status == 200 and payload["override_active"] is False
    finally:
        clear_credential_override()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_debug_credential_mutation_is_disabled_by_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SIMPLIFYNEXT_DEBUG_CREDENTIALS", "0")
    server = create_server(tmp_path / "cases", port=0, run_callback=lambda *_: None)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, _ = _request(base, "POST", "/api/debug/aws-credentials", {})
        assert status == 404
        status, _ = _request(base, "DELETE", "/api/debug/aws-credentials")
        assert status == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
