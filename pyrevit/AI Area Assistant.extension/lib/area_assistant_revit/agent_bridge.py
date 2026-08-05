"""Call the independent CPython Agent without embedding it in pyRevit."""

import json
import subprocess
import uuid


_BRIDGES = {}


def _run_process(command, input_text, cwd):
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
        stdout = process.StandardOutput.ReadToEnd()
        stderr = process.StandardError.ReadToEnd()
        if not process.WaitForExit(15000):
            process.Kill()
            raise RuntimeError("local Agent document status timed out")
        return process.ExitCode, stdout, stderr
    except ImportError:
        pass

    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout, stderr = process.communicate(input_text.encode("utf-8"))
    return (
        process.returncode,
        stdout.decode("utf-8", "replace"),
        stderr.decode("utf-8", "replace"),
    )


class AgentBridge(object):
    def __init__(self, python_executable, repository_root, runner=None):
        self._python_executable = python_executable
        self._repository_root = repository_root
        self._runner = runner or _run_process
        self._previous_document = None

    def query(self, current_document):
        request = {
            "request_id": "req-{0}".format(uuid.uuid4().hex),
            "current_document": current_document,
            "previous_document": self._previous_document,
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
        if return_code != 0 or response.get("message_type") == "error":
            raise RuntimeError(response.get("message", "local Agent document status failed"))
        if response.get("payload", {}).get("binding_status") == "bound":
            self._previous_document = current_document
        return response


def get_agent_bridge(python_executable, repository_root):
    key = (python_executable, repository_root)
    if key not in _BRIDGES:
        _BRIDGES[key] = AgentBridge(python_executable, repository_root)
    return _BRIDGES[key]
