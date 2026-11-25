"""Regression tests for the server-side review bugs: each pins an observed failure of the HTTP API."""

from __future__ import annotations

import http.client
import json
import socket
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from investigator import http_api
from investigator.http_api import create_server

FOREIGN = "http://evil.example"
JSON_HEADERS = {"Content-Type": "application/json"}


class Api:
    def __init__(self, port: int, root: Path) -> None:
        self.port = port
        self.root = root

    def request(self, method: str, path: str, body: str | None = None, headers: dict | None = None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, response.read().decode()
        finally:
            connection.close()

    def case_titles(self) -> list[str]:
        _, body = self.request("GET", "/api/cases")
        return sorted(item["title"] for item in json.loads(body)["cases"])

    def case_files(self) -> list[str]:
        return sorted(path.name for path in self.root.glob("*.json"))


@pytest.fixture
def api(tmp_path: Path) -> Iterator[Api]:
    server = create_server(tmp_path / "cases", port=0, run_callback=lambda *_: None)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield Api(server.server_address[1], tmp_path / "cases")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_foreign_origin_post_is_refused_before_a_case_is_created(api: Api) -> None:
    status, _ = api.request(
        "POST", "/api/cases", '{"title":"pwned"}', {"Origin": FOREIGN, "Content-Type": "text/plain"}
    )
    assert status == 403
    assert api.case_files() == []


def test_foreign_origin_cannot_rename_an_existing_case(api: Api) -> None:
    _, created = api.request("POST", "/api/cases", '{"title":"mine"}', JSON_HEADERS)
    case_id = json.loads(created)["caseId"]
    status, _ = api.request(
        "PATCH", f"/api/cases/{case_id}", '{"title":"stolen"}', {"Origin": FOREIGN, "Content-Type": "text/plain"}
    )
    assert status == 403
    assert api.case_titles() == ["mine"]


def test_allowed_origin_still_creates_a_case(api: Api) -> None:
    status, body = api.request(
        "POST", "/api/cases", '{"title":"ok"}', {"Origin": "http://localhost:3000", **JSON_HEADERS}
    )
    assert status == 201 and json.loads(body)["caseId"]


def test_concurrent_case_creation_keeps_every_case(api: Api) -> None:
    def create(index: int) -> tuple[int, str]:
        return api.request("POST", "/api/cases", json.dumps({"title": f"t{index}"}), JSON_HEADERS)

    with ThreadPoolExecutor(24) as pool:
        results = list(pool.map(create, range(24)))
    assert [status for status, _ in results] == [201] * 24
    assert len({json.loads(body)["caseId"] for _, body in results}) == 24
    assert api.case_titles() == sorted(f"t{index}" for index in range(24))


@pytest.mark.parametrize("suffix", ["traces", "runs"])
def test_legacy_history_routes_do_not_create_cases_or_expose_traces(api: Api, suffix: str) -> None:
    status, _ = api.request("GET", f"/api/cases/ghost/{suffix}")
    assert status == 404
    assert api.case_files() == []


def test_bad_json_and_non_object_bodies_get_a_client_error(api: Api) -> None:
    for body in ("not json", "[1, 2]"):
        status, _ = api.request("POST", "/api/runtime-settings/models", body, JSON_HEADERS)
        assert status == 400


def test_invalid_case_id_in_query_gets_a_client_error(api: Api) -> None:
    status, _ = api.request("GET", "/api/runtime-settings?caseId=bad%20id")
    assert status in {404, 422}


def _raw_post(port: int, headers: str, body: bytes) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=3) as connection:
        connection.sendall(f"POST /api/cases HTTP/1.1\r\nHost: x\r\n{headers}\r\n\r\n".encode() + body)
        return connection.recv(200)


def test_negative_content_length_is_refused_without_blocking(api: Api) -> None:
    assert _raw_post(api.port, "Content-Length: -1", b'{"title":"x"}').startswith(b"HTTP/1.0 400")


def test_oversized_body_is_refused_before_reading_it(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(http_api, "MAX_REQUEST_BODY_BYTES", 64)
    assert _raw_post(api.port, "Content-Length: 65", b"").startswith(b"HTTP/1.0 413")


def test_help_rejects_blank_message_and_unknown_case(api: Api) -> None:
    _, created = api.request("POST", "/api/cases", '{"title":"c"}', JSON_HEADERS)
    case_id = json.loads(created)["caseId"]
    status, _ = api.request("POST", f"/api/cases/{case_id}/workspace/chat", '{"message":"   "}', JSON_HEADERS)
    assert status == 422
    status, _ = api.request("POST", "/api/cases/nope/workspace/chat", '{"message":"hi"}', JSON_HEADERS)
    assert status == 404


def test_ten_candidate_sample_opens_from_the_installed_package(api: Api) -> None:
    status, body = api.request("POST", "/api/samples/open", '{"sampleId":"multi-candidate-10"}', JSON_HEADERS)
    assert status == 200
    assert len(json.loads(body)["workspace"]["students"]) == 10
