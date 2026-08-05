"""Minimal read-only MCP client and rvt-mcp document evidence gateway."""

import json
import os
from pathlib import Path
import queue
import shlex
import subprocess
import threading
import time
from typing import Any, IO, Optional

from .document_binding import RvtMcpSnapshot


DOCUMENT_EVIDENCE_CODE = """return new {
    documentTitle = doc.Title,
    documentPath = doc.PathName,
    projectInformationId = doc.ProjectInformation.UniqueId,
    isModified = doc.IsModified,
    activeViewId = uidoc.ActiveView.Id.Value.ToString(),
    activeViewName = uidoc.ActiveView.Name
};"""


def discover_rvt_mcp_command() -> list[str]:
    configured = os.environ.get("AI_AREA_ASSISTANT_RVT_MCP_COMMAND", "").strip()
    if configured:
        return shlex.split(configured, posix=False)

    local_app_data = os.environ.get("LOCALAPPDATA", "")
    server_root = Path(local_app_data) / "RvtMcp" / "rvt" / "server"
    candidates = sorted(server_root.glob("*/rvt-mcp.exe"), reverse=True)
    if not candidates:
        raise RuntimeError(
            "rvt-mcp server not found; set AI_AREA_ASSISTANT_RVT_MCP_COMMAND"
        )
    return [str(candidates[0])]


class McpStdioClient:
    def __init__(self, command: Optional[list[str]] = None, timeout_seconds: float = 30.0):
        self._command = command or discover_rvt_mcp_command()
        self._process: Optional[subprocess.Popen[str]] = None
        self._next_id = 1
        self._timeout_seconds = timeout_seconds
        self._messages: queue.Queue[Optional[str]] = queue.Queue()
        self._reader: Optional[threading.Thread] = None
        self._deadline: Optional[float] = None

    def __enter__(self) -> "McpStdioClient":
        self._deadline = time.monotonic() + self._timeout_seconds
        self._process = subprocess.Popen(
            self._command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self._reader = threading.Thread(target=self._read_stdout_lines, daemon=True)
        self._reader.start()
        self._request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {
                    "name": "revit-ai-area-assistant-agent",
                    "version": "0.1.0",
                },
            },
        )
        self._notify("notifications/initialized", {})
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self._process is not None:
            self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None

    def call_tool(self, name: str, arguments: dict) -> dict:
        result = self._request(
            "tools/call",
            {"name": name, "arguments": arguments},
        )
        if result.get("isError"):
            raise RuntimeError("rvt-mcp tool failed: " + name)
        for item in result.get("content", []):
            if item.get("type") == "text":
                try:
                    return json.loads(item["text"])
                except json.JSONDecodeError as error:
                    raise RuntimeError("rvt-mcp tool failed: " + name) from error
        raise RuntimeError("rvt-mcp tool returned no JSON text: " + name)

    def _request(self, method: str, params: dict) -> dict:
        request_id = self._next_id
        self._next_id += 1
        self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        while True:
            message = self._read()
            if message.get("id") != request_id:
                continue
            if "error" in message:
                error = message["error"]
                raise RuntimeError(
                    "MCP request failed ({0}): {1}".format(
                        error.get("code", "unknown"),
                        error.get("message", "unknown error"),
                    )
                )
            return message["result"]

    def _notify(self, method: str, params: dict) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def _write(self, message: dict) -> None:
        stdin, _ = self._streams()
        stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        stdin.flush()

    def _read(self) -> dict:
        timeout = self._timeout_seconds
        if self._deadline is not None:
            timeout = max(0.0, self._deadline - time.monotonic())
        try:
            line = self._messages.get(timeout=timeout)
        except queue.Empty as error:
            raise RuntimeError("rvt-mcp response timed out") from error
        if line is None:
            raise RuntimeError("rvt-mcp server closed the connection")
        return json.loads(line)

    def _read_stdout_lines(self) -> None:
        if self._process is None or self._process.stdout is None:
            self._messages.put(None)
            return
        for line in self._process.stdout:
            self._messages.put(line)
        self._messages.put(None)

    def _streams(self) -> tuple[IO[str], IO[str]]:
        if self._process is None or self._process.stdin is None or self._process.stdout is None:
            raise RuntimeError("MCP client is not started")
        return self._process.stdin, self._process.stdout


def read_current_revit_evidence(client: Any) -> RvtMcpSnapshot:
    discovery = client.call_tool("revit_list_available_targets", {})
    targets = discovery.get("targets", [])
    if discovery.get("count") != 1 or len(targets) != 1:
        raise RuntimeError("document binding requires exactly one Revit target")

    target = targets[0]
    switched = client.call_tool(
        "revit_switch_target",
        {"version": str(target["year"]), "verify": True},
    )
    if not switched.get("ok") or not switched.get("verified"):
        raise RuntimeError("rvt-mcp target verification failed")
    result = client.call_tool(
        "revit_send_code_to_revit",
        {"code": DOCUMENT_EVIDENCE_CODE},
    )
    if not result.get("executed") or not isinstance(result.get("result"), dict):
        raise RuntimeError("rvt-mcp document evidence failed")
    return RvtMcpSnapshot.from_tool_results(target, result["result"])
