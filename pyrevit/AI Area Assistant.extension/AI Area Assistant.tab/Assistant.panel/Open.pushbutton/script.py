"""Show the dockable AI Area Assistant panel registered at startup."""

import os
import sys

from pyrevit import forms

current = os.path.dirname(__file__)
while current and not current.endswith(".extension"):
    parent = os.path.dirname(current)
    if parent == current:
        raise RuntimeError("AI Area Assistant extension root was not found.")
    current = parent
repository_root = os.path.dirname(os.path.dirname(current))
if repository_root not in sys.path:
    sys.path.insert(0, repository_root)

from area_assistant_pyrevit import PANEL_ID

forms.open_dockable_panel(PANEL_ID)
