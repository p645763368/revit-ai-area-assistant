"""Start the standalone CPython Agent without opening a console window."""

import os
import shutil
import subprocess
import sys


def _python_command():
    configured = os.environ.get("AI_AREA_ASSISTANT_PYTHON")
    if configured:
        return [configured]
    executable = sys.executable
    if executable and os.path.basename(executable).lower().startswith("python"):
        return [executable]
    launcher = shutil.which("py")
    if launcher:
        return [launcher, "-3"]
    python = shutil.which("python")
    if python:
        return [python]
    raise RuntimeError(
        "No CPython interpreter found. Set AI_AREA_ASSISTANT_PYTHON to Python 3.9+."
    )

def start_agent_process(repository_root):
    command = _python_command() + ["-m", "area_assistant_agent", "--serve"]
    return subprocess.Popen(
        command,
        cwd=repository_root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
