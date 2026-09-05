import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

from investigator.http_api import create_server
from investigator.llm.bedrock import clear_credential_override
from investigator.runtime_settings import reset_model_overrides


def request(base: str, method: str, path: str, payload: dict | None = None, origin: str | None = None):
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if origin:
        headers["Origin"] = origin
    req = urllib.request.Request(base + path, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req) as response:
            raw = response.read()
            return response.status, dict(response.headers), (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), json.loads(error.read())


def server(tmp_path: Path):
    clear_credential_override()
    reset_model_overrides()
    instance = create_server(tmp_path / "cases", port=0, run_callback=lambda *_: None, run_mode="vnext")
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    return instance, thread, f"http://127.0.0.1:{instance.server_address[1]}"


def test_normal_runtime_settings_exposes_only_approved_roles_and_models(tmp_path, monkeypatch):
    monkeypatch.delenv("SIMPLIFYNEXT_DEBUG_CREDENTIALS", raising=False)
    instance, thread, base = server(tmp_path)
    try:
        status, _, payload = request(base, "GET", "/api/runtime-settings")
        assert status == 200
        assert [item["model"] for item in payload["availableModels"]] == ["anthropic.claude-sonnet-4-5", "anthropic.claude-opus-4-5"]
        assert set(payload["models"]) == {"investigator", "workspaceHelp"}
        assert payload["models"]["investigator"]["effectiveModel"] == "anthropic.claude-sonnet-4-5"
        serialized = json.dumps(payload)
        for forbidden in ("steward", "Steward", "Warden", "haiku", "nova", "deepseek", "qwen", "glm", "kimi", "gpt-oss"):
            assert forbidden not in serialized.lower()
    finally:
        instance.shutdown(); instance.server_close(); thread.join(timeout=2)


def test_model_update_is_atomic_and_rejects_unapproved_values(tmp_path):
    instance, thread, base = server(tmp_path)
    try:
        status, _, _ = request(base, "POST", "/api/runtime-settings/models", {"investigator": "anthropic.claude-opus-4-5", "workspaceHelp": "anthropic.claude-haiku-4-5"})
        assert status == 422
        _, _, payload = request(base, "GET", "/api/runtime-settings")
        assert payload["models"]["investigator"]["effectiveModel"] == "anthropic.claude-sonnet-4-5"
        assert payload["models"]["workspaceHelp"]["effectiveModel"] == "anthropic.claude-sonnet-4-5"
        status, _, payload = request(base, "POST", "/api/runtime-settings/models", {"investigator": "anthropic.claude-opus-4-5", "workspaceHelp": "anthropic.claude-sonnet-4-5"})
        assert status == 200 and payload["models"]["investigator"]["effectiveModel"] == "anthropic.claude-opus-4-5"
        status, _, payload = request(base, "POST", "/api/runtime-settings/models/reset", {})
        assert status == 200 and all(item["effectiveModel"] == "anthropic.claude-sonnet-4-5" for item in payload["models"].values())
    finally:
        instance.shutdown(); instance.server_close(); thread.join(timeout=2)


def test_normal_credential_routes_are_safe_and_cors_is_allow_listed(tmp_path):
    instance, thread, base = server(tmp_path)
    secrets = {"aws_access_key_id": "FAKE_ACCESS_SECRET", "aws_secret_access_key": "FAKE_SECRET_VALUE", "aws_session_token": "FAKE_TOKEN_VALUE"}
    try:
        status, headers, payload = request(base, "OPTIONS", "/api/runtime-settings/models", origin="http://127.0.0.1:3000")
        assert status == 204 and "POST" in headers["Access-Control-Allow-Methods"] and "DELETE" in headers["Access-Control-Allow-Methods"]
        status, _, payload = request(base, "POST", "/api/runtime-settings/aws-credentials", secrets)
        assert status == 200 and payload["aws"]["mode"] == "temporary_credentials"
        assert all(secret not in json.dumps(payload) for secret in secrets.values())
        status, _, payload = request(base, "GET", "/api/runtime-settings")
        assert status == 200 and payload["aws"]["mode"] == "temporary_credentials"
        assert all(secret not in json.dumps(payload) for secret in secrets.values())
        status, _, payload = request(base, "DELETE", "/api/runtime-settings/aws-credentials")
        assert status == 200 and payload["aws"]["mode"] == "default_chain"
        _, disallowed_headers, _ = request(base, "OPTIONS", "/api/runtime-settings", origin="https://not-allowed.example")
        assert "Access-Control-Allow-Origin" not in disallowed_headers
    finally:
        clear_credential_override(); instance.shutdown(); instance.server_close(); thread.join(timeout=2)
