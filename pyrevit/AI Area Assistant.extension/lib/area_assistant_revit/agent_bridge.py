"""Call the independent CPython Agent without embedding it in pyRevit."""

import json
import os
import subprocess
import tempfile
import threading
import time
import uuid


CONTRACT_VERSION = "1.0"
PROCESS_TIMEOUT_SECONDS = 15
_BRIDGES = {}


def _run_process(command, input_text, cwd, timeout_seconds=PROCESS_TIMEOUT_SECONDS):
    try:
        from System.Diagnostics import Process, ProcessStartInfo

        start_info = ProcessStartInfo()
        start_info.FileName = command[0]
        start_info.Arguments = " ".join(command[1:])
        start_info.WorkingDirectory = cwd
        start_info.UseShellExecute = False
        start_info.CreateNoWindow = True
        start_info.RedirectStandardInput = True
        start_info.RedirectStandardOutput = True
        start_info.RedirectStandardError = True

        process = Process()
        process.StartInfo = start_info
        process.Start()
        process.StandardInput.Write(input_text)
        process.StandardInput.Close()
        output = {"stdout": "", "stderr": ""}
        stdout_thread = threading.Thread(
            target=lambda: output.__setitem__("stdout", process.StandardOutput.ReadToEnd())
        )
        stderr_thread = threading.Thread(
            target=lambda: output.__setitem__("stderr", process.StandardError.ReadToEnd())
        )
        stdout_thread.daemon = True
        stderr_thread.daemon = True
        stdout_thread.start()
        stderr_thread.start()
        if not process.WaitForExit(int(timeout_seconds * 1000)):
            process.Kill()
            process.WaitForExit()
            raise RuntimeError("local Agent document status timed out")
        stdout_thread.join(1)
        stderr_thread.join(1)
        return process.ExitCode, output["stdout"], output["stderr"]
    except ImportError:
        pass

    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    output = {}

    def communicate():
        output["streams"] = process.communicate(input_text.encode("utf-8"))

    worker = threading.Thread(target=communicate)
    worker.daemon = True
    worker.start()
    worker.join(timeout_seconds)
    if worker.is_alive():
        process.kill()
        worker.join(1)
        raise RuntimeError("local Agent document status timed out")
    stdout, stderr = output["streams"]
    return (
        process.returncode,
        stdout.decode("utf-8", "replace"),
        stderr.decode("utf-8", "replace"),
    )


def _public_document(document):
    return {
        "revit_instance_id": document["revit_instance_id"],
        "document_title": document["document_title"],
        "document_path": document["document_path"],
        "document_fingerprint": document["document_fingerprint"],
        "active_view": document["active_view"],
        "is_modified": document["is_modified"],
    }


class AgentBridge(object):
    def __init__(
        self,
        python_executable,
        repository_root,
        runner=None,
        background=False,
        result_root=None,
    ):
        self._python_executable = python_executable
        self._repository_root = repository_root
        self._runner = runner or _run_process
        self._background = background
        self._previous_document = None
        self._pause_reason = None
        self._worker = None
        self._result = None
        self._error = None
        self._request_key = None
        self._lock = threading.Lock()
        self._result_root = result_root or os.path.join(
            os.environ.get("LOCALAPPDATA", tempfile.gettempdir()),
            "RevitAIAreaAssistant",
            "runtime",
        )

    def query(self, current_document, observed_pause_reason=None):
        if observed_pause_reason:
            self._pause_reason = observed_pause_reason
        if not self._background:
            return self._execute(current_document)

        request_key = (
            current_document.get("revit_instance_id"),
            current_document.get("document_fingerprint"),
            current_document.get("active_view", {}).get("id"),
            current_document.get("is_modified"),
            self._pause_reason,
        )
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return None
            cached = self._read_cached_result(current_document)
            if cached is not None and cached.get("request_key") == list(request_key):
                state = cached.get("state")
                if state == "completed":
                    response = cached["response"]
                    self._remember_response(current_document, response)
                    return response
                if state == "error":
                    raise RuntimeError(cached.get("error", "local Agent document status failed"))
                if state == "running" and time.time() - cached.get("updated_at", 0) < 20:
                    return None
            if self._request_key == request_key:
                if self._error is not None:
                    raise self._error
                if self._result is not None:
                    return self._result
            self._request_key = request_key
            self._result = None
            self._error = None
            self._write_cached_result(
                current_document,
                {
                    "request_key": list(request_key),
                    "state": "running",
                    "updated_at": time.time(),
                },
            )
            self._worker = threading.Thread(
                target=self._execute_in_background,
                args=(current_document,),
            )
            self._worker.daemon = True
            self._worker.start()
            return None

    def _execute_in_background(self, current_document):
        try:
            result = self._execute(current_document)
            with self._lock:
                self._result = result
                self._write_cached_result(
                    current_document,
                    {
                        "request_key": list(self._request_key),
                        "state": "completed",
                        "updated_at": time.time(),
                        "response": result,
                    },
                )
        except Exception as error:
            with self._lock:
                self._error = error
                self._write_cached_result(
                    current_document,
                    {
                        "request_key": list(self._request_key),
                        "state": "error",
                        "updated_at": time.time(),
                        "error": str(error),
                    },
                )

    def _execute(self, current_document):
        request_id = "req-{0}".format(uuid.uuid4().hex)
        request = {
            "contract_version": CONTRACT_VERSION,
            "message_type": "request",
            "request_id": request_id,
            "action": "revit.document_status",
            "payload": {
                "current_document": _public_document(current_document),
                "previous_document": self._previous_document,
                "previous_pause_reason": self._pause_reason,
            },
        }
        command = [
            self._python_executable,
            "-m",
            "area_assistant_agent",
            "--document-status",
        ]
        return_code, stdout, _ = self._runner(
            command,
            json.dumps(request, ensure_ascii=False),
            self._repository_root,
        )
        if not stdout.strip():
            raise RuntimeError("local Agent returned no document status")
        response = json.loads(stdout)
        if response.get("contract_version") != CONTRACT_VERSION:
            raise RuntimeError("unsupported local Agent contract version")
        if response.get("request_id") != request_id:
            raise RuntimeError("local Agent response request id mismatch")
        if return_code != 0 or response.get("message_type") == "error":
            raise RuntimeError(response.get("message", "local Agent document status failed"))
        if response.get("message_type") != "response" or response.get("status") != "completed":
            raise RuntimeError("invalid local Agent document status response")
        self._remember_response(current_document, response)
        return response

    def _remember_response(self, current_document, response):
        binding = response.get("payload", {})
        if binding.get("binding_status") == "bound":
            self._previous_document = _public_document(current_document)
        if binding.get("binding_status") == "paused":
            self._pause_reason = binding.get("pause_reason") or "document_changed"

    def _result_path(self, current_document):
        instance_id = current_document.get("revit_instance_id", "invalid")
        safe_instance_id = "".join(
            character for character in instance_id if character.isalnum() or character in "-_"
        )
        return os.path.join(self._result_root, "pyrevit-{0}.json".format(safe_instance_id))

    def _read_cached_result(self, current_document):
        path = self._result_path(current_document)
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "r") as stream:
                return json.load(stream)
        except (IOError, ValueError):
            return None

    def _write_cached_result(self, current_document, value):
        if not os.path.isdir(self._result_root):
            os.makedirs(self._result_root)
        path = self._result_path(current_document)
        temporary = path + ".tmp"
        with open(temporary, "w") as stream:
            json.dump(value, stream, ensure_ascii=True, sort_keys=True)
        if os.path.isfile(path):
            os.remove(path)
        os.rename(temporary, path)


def get_agent_bridge(python_executable, repository_root):
    key = (python_executable, repository_root)
    if key not in _BRIDGES:
        _BRIDGES[key] = AgentBridge(
            python_executable,
            repository_root,
            background=True,
        )
    return _BRIDGES[key]
