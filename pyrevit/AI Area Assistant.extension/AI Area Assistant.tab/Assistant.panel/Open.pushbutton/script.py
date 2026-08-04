"""Minimal read-only pyRevit entry point.

The dockable pane and Agent connection belong to later issues. This command
only proves that the extension is discoverable and executable in pyRevit.
"""

from pyrevit import forms


forms.alert(
    "Engineering baseline is ready. The dockable panel is delivered by a later issue.",
    title="AI Area Assistant",
    warn_icon=False,
)
