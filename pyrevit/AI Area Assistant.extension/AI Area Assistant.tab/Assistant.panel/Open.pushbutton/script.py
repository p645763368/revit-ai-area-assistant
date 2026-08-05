"""Show the current Revit document identity and safe binding status."""

__persistentengine__ = True

import os
import sys

from pyrevit import forms, revit

try:
    from pyrevit import HOST_APP, UI, framework, script as pyrevit_script
except ImportError:
    HOST_APP = None
    UI = None
    framework = None
    pyrevit_script = None


extension_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
extension_lib = os.path.join(extension_root, "lib")
if extension_lib not in sys.path:
    sys.path.insert(0, extension_lib)

from area_assistant_revit.agent_bridge import get_agent_bridge
from area_assistant_revit.document_status import collect_document_status


BOUND_FINGERPRINT_KEY = "AI_AREA_ASSISTANT_BOUND_DOCUMENT_FINGERPRINT"
PAUSE_REASON_KEY = "AI_AREA_ASSISTANT_DOCUMENT_PAUSE_REASON"
VIEW_HANDLER_KEY = "AI_AREA_ASSISTANT_VIEW_HANDLER"


def _on_view_activated(sender, event_args):
    try:
        bound_fingerprint = pyrevit_script.get_envvar(BOUND_FINGERPRINT_KEY)
        if not bound_fingerprint:
            return
        current = collect_document_status(
            event_args.CurrentActiveView.Document,
            os.environ.get("AI_AREA_ASSISTANT_TEST_DOCUMENT", ""),
        )
        if current["document_fingerprint"] != bound_fingerprint:
            pyrevit_script.set_envvar(PAUSE_REASON_KEY, "document_changed")
    except Exception:
        pyrevit_script.set_envvar(PAUSE_REASON_KEY, "document_observation_failed")


def _ensure_document_switch_guard():
    if pyrevit_script is None or pyrevit_script.get_envvar(VIEW_HANDLER_KEY):
        return
    handler = framework.EventHandler[UI.Events.ViewActivatedEventArgs](_on_view_activated)
    HOST_APP.uiapp.ViewActivated += handler
    pyrevit_script.set_envvar(VIEW_HANDLER_KEY, handler)


status = collect_document_status(
    revit.doc,
    os.environ.get("AI_AREA_ASSISTANT_TEST_DOCUMENT", ""),
)
path_match = "yes" if status["authorized_path_match"] else "no"
view = status["active_view"]
binding_status = "pending"
rvt_mcp_status = "unchecked"
write_permission = "denied"
pause_reason = None
local_pause_reason = (
    pyrevit_script.get_envvar(PAUSE_REASON_KEY) if pyrevit_script is not None else None
)
agent_python = os.environ.get("AI_AREA_ASSISTANT_AGENT_PYTHON", "").strip()
repository_root = os.environ.get(
    "AI_AREA_ASSISTANT_REPOSITORY_ROOT",
    os.path.dirname(os.path.dirname(extension_root)),
)
if agent_python:
    try:
        agent_response = get_agent_bridge(agent_python, repository_root).query(
            status,
            local_pause_reason,
        )
        if agent_response is not None:
            binding = agent_response["payload"]
            binding_status = binding["binding_status"]
            rvt_mcp_status = binding["rvt_mcp_status"]
            write_permission = "allowed" if binding["write_allowed"] else "denied"
            pause_reason = binding["pause_reason"]
            if binding_status == "bound" and pyrevit_script is not None:
                pyrevit_script.set_envvar(
                    BOUND_FINGERPRINT_KEY,
                    status["document_fingerprint"],
                )
                _ensure_document_switch_guard()
        else:
            pause_reason = "verification_running_close_wait_and_click_again"
    except Exception as error:
        binding_status = "unavailable"
        pause_reason = "agent_error: {0}".format(error)

if local_pause_reason:
    binding_status = "paused"
    write_permission = "denied"
    pause_reason = local_pause_reason

message = "\n".join(
    [
        "Revit instance: {0}".format(status["revit_instance_id"]),
        "Document title: {0}".format(status["document_title"]),
        "Document path: {0}".format(status["document_path"] or "<unsaved>"),
        "Active view: {0} ({1})".format(view["name"], view["id"]),
        "IsModified: {0}".format(status["is_modified"]),
        "Authorized path match: {0}".format(path_match),
        "Agent/rvt-mcp binding: {0}".format(binding_status),
        "rvt-mcp status: {0}".format(rvt_mcp_status),
        "Write permission: {0}".format(write_permission),
        "Pause reason: {0}".format(pause_reason or "none"),
    ]
)


forms.alert(
    message,
    title="AI Area Assistant",
    warn_icon=False,
)
