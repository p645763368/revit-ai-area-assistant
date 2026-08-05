#! python3
"""Register the AI Area Assistant dockable pane once per Revit session."""

import os
import sys


EXTENSION_ROOT = os.path.dirname(__file__)
REPOSITORY_ROOT = os.path.dirname(os.path.dirname(EXTENSION_ROOT))
if REPOSITORY_ROOT not in sys.path:
    sys.path.insert(0, REPOSITORY_ROOT)

from pyrevit import forms
from area_assistant_pyrevit.panel import AiAreaAssistantPanel


if not forms.is_registered_dockable_panel(AiAreaAssistantPanel):
    forms.register_dockable_panel(AiAreaAssistantPanel, default_visible=False)
