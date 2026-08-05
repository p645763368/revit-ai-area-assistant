"""Start the standalone CPython Agent without opening a console window."""

import os
import subprocess
import sys
from distutils.spawn import find_executable


def _python_command():
    configured = os.environ.get("AI_AREA_ASSISTANT_PYTHON")
    if configured:
        return [configured]
    executable = sys.executable
    if executable and os.path.basename(executable).lower().startswith("python"):
        return [executable]
    launcher = find_executable("py")
    if launcher:
        return [launcher, "-3"]
    python = find_executable("python")
    if python:
        return [python]
    raise RuntimeError(
        "No CPython interpreter found. Set AI_AREA_ASSISTANT_PYTHON to Python 3.9+."
    )

def start_agent_process(repository_root):
    command = _python_command() + ["-m", "area_assistant_agent", "--serve"]
    with open(os.devnull, "rb") as null_input, open(os.devnull, "ab") as null_output:
        return subprocess.Popen(
            command,
            cwd=repository_root,
            stdin=null_input,
            stdout=null_output,
            stderr=null_output,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
