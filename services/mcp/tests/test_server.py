"""Tests for the studio-mcp JSON-RPC server and HTTP client."""

import io
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from studio_mcp.server import (  # noqa: E402
    TOOLS,
    StudioAPIError,
    StudioClient,
    call_tool,
    handle_message,
    serve,
)


class FakeClient:
    """Records requests and returns canned bodies."""

    def __init__(self, body: str = "{}", error: StudioAPIError | None = None) -> None:
        self.body = body
        self.error = error
        self.calls: list[tuple[str, str, object]] = []

    def get(self, path: str, params: object = None) -> str:
        self.calls.append(("GET", path, params))
        if self.error:
            raise self.error
        return self.body

    def post(self, path: str, payload: object = None) -> str:
        self.calls.append(("POST", path, payload))
        if self.error:
            raise self.error
        return self.body


def request(method: str, params: object = None, message_id: object = 1) -> dict:
    message: dict = {"jsonrpc": "2.0", "method": method}
    if message_id is not None:
        message["id"] = message_id
    if params is not None:
        message["params"] = params
    return message


def test_initialize_echoes_protocol_version() -> None:
    response = handle_message(
        request("initialize", {"protocolVersion": "2025-03-26", "capabilities": {}}),
        FakeClient(),
    )
    assert response["result"]["protocolVersion"] == "2025-03-26"
    assert response["result"]["serverInfo"]["name"] == "skills-video-studio"
    assert "tools" in response["result"]["capabilities"]


def test_initialize_defaults_protocol_version() -> None:
    response = handle_message(request("initialize", {}), FakeClient())
    assert response["result"]["protocolVersion"] == "2025-06-18"


def test_tools_list_exposes_all_tools_with_schemas() -> None:
    response = handle_message(request("tools/list"), FakeClient())
    tools = response["result"]["tools"]
    assert [tool["name"] for tool in tools] == [tool["name"] for tool in TOOLS]
    assert all("inputSchema" in tool and "description" in tool for tool in tools)
    assert len(tools) == 9


def test_notifications_get_no_response() -> None:
    notification = request("notifications/initialized", message_id=None)
    assert handle_message(notification, FakeClient()) is None


def test_unknown_method_errors() -> None:
    response = handle_message(request("resources/list"), FakeClient())
    assert response["error"]["code"] == -32601


def test_tools_call_wraps_response_text() -> None:
    client = FakeClient(body='[{"id": "explainer-video"}]')
    response = handle_message(
        request("tools/call", {"name": "list_recipes", "arguments": {}}), client
    )
    result = response["result"]
    assert result["isError"] is False
    assert result["content"][0]["text"] == '[{"id": "explainer-video"}]'


def test_tools_call_api_error_sets_is_error() -> None:
    client = FakeClient(error=StudioAPIError(404, "Job not found"))
    response = handle_message(
        request("tools/call", {"name": "get_job", "arguments": {"job_id": "missing"}}), client
    )
    result = response["result"]
    assert result["isError"] is True
    assert "HTTP 404" in result["content"][0]["text"]
    assert "Job not found" in result["content"][0]["text"]


def test_tools_call_unknown_tool_is_invalid_params() -> None:
    response = handle_message(
        request("tools/call", {"name": "rm_rf", "arguments": {}}), FakeClient()
    )
    assert response["error"]["code"] == -32602


def test_tools_call_missing_required_argument() -> None:
    response = handle_message(
        request("tools/call", {"name": "run_phase", "arguments": {"phase": "narrate"}}),
        FakeClient(),
    )
    assert response["error"]["code"] == -32602
    assert "project_id" in response["error"]["message"]


def test_tool_routing_and_quoting() -> None:
    client = FakeClient()
    call_tool(client, "run_phase", {"project_id": "p/1", "phase": "render"})
    call_tool(client, "get_job_log", {"job_id": "j1", "tail": 40})
    call_tool(client, "create_project", {"name": "Demo"})
    assert client.calls[0] == ("POST", "/api/v1/projects/p%2F1/jobs", {"phase": "render"})
    assert client.calls[1] == ("GET", "/api/v1/jobs/j1/log", {"tail": 40})
    assert client.calls[2] == ("POST", "/api/v1/projects", {"name": "Demo"})


def test_serve_loop_handles_parse_errors_and_requests() -> None:
    stdin = io.StringIO(
        "not json\n"
        + json.dumps(request("ping"))
        + "\n\n"
        + json.dumps(request("notifications/initialized", message_id=None))
        + "\n"
    )
    stdout = io.StringIO()
    serve(stdin=stdin, stdout=stdout, client=FakeClient())
    lines = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert lines[0]["error"]["code"] == -32700
    assert lines[1] == {"jsonrpc": "2.0", "id": 1, "result": {}}
    assert len(lines) == 2


class _StubStudioHandler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: str, content_type: str = "application/json") -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path == "/api/v1/projects":
            self._send(200, '[{"id": "p1"}]')
        elif self.path.startswith("/api/v1/jobs/j1/log"):
            self._send(200, "line one\nline two\n", content_type="text/plain")
        else:
            self._send(404, '{"detail": "Project not found"}')

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        self._send(202, json.dumps({"echo": body}))

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture()
def stub_api() -> str:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubStudioHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    thread.join(timeout=5)


def test_client_get_post_and_errors(stub_api: str) -> None:
    client = StudioClient(base_url=stub_api)
    assert json.loads(client.get("/api/v1/projects")) == [{"id": "p1"}]
    assert client.get("/api/v1/jobs/j1/log", {"tail": 2}) == "line one\nline two\n"
    echoed = json.loads(client.post("/api/v1/projects", {"name": "Demo"}))
    assert echoed == {"echo": {"name": "Demo"}}
    with pytest.raises(StudioAPIError) as excinfo:
        client.get("/api/v1/projects/missing")
    assert excinfo.value.status == 404
    assert excinfo.value.detail == "Project not found"


def test_client_connection_error_is_wrapped() -> None:
    client = StudioClient(base_url="http://127.0.0.1:1", timeout=0.5)
    with pytest.raises(StudioAPIError) as excinfo:
        client.get("/health")
    assert excinfo.value.status == 0
    assert "cannot reach the studio API" in excinfo.value.detail
