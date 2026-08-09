"""MCP stdio server for the Skills Video Studio API.

Speaks newline-delimited JSON-RPC 2.0 on stdin/stdout and forwards tool
calls to a running Studio instance over HTTP. Stdlib only: the studio
remains the single control plane; this process owns no state and runs no
commands itself.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from . import __version__

DEFAULT_API_URL = "http://localhost:8080"
FALLBACK_PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "skills-video-studio", "version": __version__}
PHASES = ["prepare", "narrate", "measure", "compose", "validate", "snapshot", "render", "qa"]


class StudioAPIError(RuntimeError):
    """A failed or unreachable Studio API call."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


class StudioClient:
    """Minimal JSON-over-HTTP client for the Studio REST API."""

    def __init__(self, base_url: str | None = None, timeout: float = 30.0) -> None:
        configured = base_url or os.environ.get("STUDIO_API_URL", DEFAULT_API_URL)
        self.base_url = configured.rstrip("/")
        self.timeout = timeout

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> str:
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        body = None
        headers = {"Accept": "application/json, text/plain"}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as error:
            raw = error.read().decode("utf-8", errors="replace")
            try:
                detail = json.loads(raw).get("detail", raw)
            except (json.JSONDecodeError, AttributeError):
                detail = raw or error.reason
            raise StudioAPIError(error.code, str(detail)) from error
        except urllib.error.URLError as error:
            raise StudioAPIError(
                0, f"cannot reach the studio API at {self.base_url}: {error.reason}"
            ) from error

    def get(self, path: str, params: dict[str, Any] | None = None) -> str:
        return self._request("GET", path, params=params)

    def post(self, path: str, payload: dict[str, Any] | None = None) -> str:
        return self._request("POST", path, payload=payload or {})


def _quoted(identifier: str) -> str:
    return urllib.parse.quote(identifier, safe="")


TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_recipes",
        "description": "List the studio's production recipes and their ordered phases.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "create_project",
        "description": (
            "Create a studio project and its workspace manifest. "
            "Returns the project record including its id and path."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Human-readable project name"},
                "recipe": {
                    "type": "string",
                    "description": "Recipe id from list_recipes (default explainer-video)",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "list_projects",
        "description": "List studio projects with ids, recipes, and workspace paths.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_project",
        "description": (
            "Get one project including per-phase status (which phases succeeded and when), "
            "so you can decide what to run or rerun."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"project_id": {"type": "string"}},
            "required": ["project_id"],
        },
    },
    {
        "name": "run_phase",
        "description": (
            "Start one production phase as a background job and return immediately "
            "with the job record. Poll get_job until status is succeeded, failed, or "
            "canceled; read get_job_log for output. Phases run the recipe's trusted "
            "commands only."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "phase": {"type": "string", "enum": PHASES},
            },
            "required": ["project_id", "phase"],
        },
    },
    {
        "name": "get_job",
        "description": "Get a job's status, exit code, timestamps, and command.",
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
    },
    {
        "name": "get_job_log",
        "description": "Fetch a job's captured log output; pass tail to limit to the last N lines.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "tail": {"type": "integer", "minimum": 1, "maximum": 10000},
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "cancel_job",
        "description": "Request cancellation of a queued or running job.",
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
    },
    {
        "name": "list_artifacts",
        "description": (
            "List a project's production artifacts (paths, sizes, timestamps). "
            "Artifacts stay on disk in the project workspace; fetch them there."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"project_id": {"type": "string"}},
            "required": ["project_id"],
        },
    },
]

_TOOL_INDEX = {tool["name"]: tool for tool in TOOLS}


def call_tool(client: StudioClient, name: str, arguments: dict[str, Any]) -> str:
    """Dispatch one tool call to the Studio API and return the response body."""
    tool = _TOOL_INDEX.get(name)
    if tool is None:
        raise ValueError(f"Unknown tool: {name}")
    for required in tool["inputSchema"]["required"]:
        if required not in arguments:
            raise ValueError(f"{name} requires argument {required!r}")

    if name == "list_recipes":
        return client.get("/api/v1/recipes")
    if name == "create_project":
        payload = {"name": arguments["name"]}
        if "recipe" in arguments:
            payload["recipe"] = arguments["recipe"]
        return client.post("/api/v1/projects", payload)
    if name == "list_projects":
        return client.get("/api/v1/projects")
    if name == "get_project":
        return client.get(f"/api/v1/projects/{_quoted(arguments['project_id'])}")
    if name == "run_phase":
        return client.post(
            f"/api/v1/projects/{_quoted(arguments['project_id'])}/jobs",
            {"phase": arguments["phase"]},
        )
    if name == "get_job":
        return client.get(f"/api/v1/jobs/{_quoted(arguments['job_id'])}")
    if name == "get_job_log":
        params = {"tail": arguments["tail"]} if "tail" in arguments else None
        return client.get(f"/api/v1/jobs/{_quoted(arguments['job_id'])}/log", params)
    if name == "cancel_job":
        return client.post(f"/api/v1/jobs/{_quoted(arguments['job_id'])}/cancel")
    if name == "list_artifacts":
        return client.get(f"/api/v1/projects/{_quoted(arguments['project_id'])}/artifacts")
    raise ValueError(f"Unhandled tool: {name}")


def handle_message(message: Any, client: StudioClient) -> dict[str, Any] | None:
    """Handle one JSON-RPC message; return the response, or None for notifications."""
    if not isinstance(message, dict):
        return _error(None, -32600, "Invalid request")
    method = message.get("method")
    message_id = message.get("id")
    is_notification = "id" not in message

    if not isinstance(method, str):
        return None if is_notification else _error(message_id, -32600, "Invalid request")
    if method.startswith("notifications/"):
        return None

    if method == "initialize":
        params = message.get("params") or {}
        requested = params.get("protocolVersion")
        result: dict[str, Any] = {
            "protocolVersion": requested if isinstance(requested, str) else (
                FALLBACK_PROTOCOL_VERSION
            ),
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name", "")
        arguments = params.get("arguments") or {}
        try:
            text = call_tool(client, name, arguments)
            result = {"content": [{"type": "text", "text": text}], "isError": False}
        except ValueError as error:
            return _error(message_id, -32602, str(error))
        except StudioAPIError as error:
            label = f"HTTP {error.status}" if error.status else "connection error"
            result = {
                "content": [{"type": "text", "text": f"Studio API {label}: {error.detail}"}],
                "isError": True,
            }
    else:
        if is_notification:
            return None
        return _error(message_id, -32601, f"Method not found: {method}")

    if is_notification:
        return None
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def _error(message_id: Any, code: int, text: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": text}}


def serve(stdin: Any = None, stdout: Any = None, client: StudioClient | None = None) -> None:
    """Run the newline-delimited JSON-RPC loop until stdin closes."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    client = client or StudioClient()
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            response: dict[str, Any] | None = _error(None, -32700, "Parse error")
        else:
            response = handle_message(message, client)
        if response is not None:
            stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
            stdout.flush()


def main() -> None:
    """Console entry point."""
    serve()


if __name__ == "__main__":
    main()
