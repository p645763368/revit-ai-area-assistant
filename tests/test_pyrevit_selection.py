import types
import unittest
import sys
from unittest.mock import patch

from area_assistant_pyrevit.selection import (
    SelectionCancelled,
    collect_allowed_elements,
    format_selection_summary,
    highlight_elements,
    summarize_element,
)


class _Id:
    def __init__(self, value):
        self.Value = value

    def __str__(self):
        return str(self.Value)


class _Document:
    def __init__(self, related):
        self._related = related

    def GetElement(self, element_id):
        return self._related.get(element_id.Value)

    def Equals(self, other):
        return self is other


def _element(category, element_id=101, type_name="Generic", level_name="Level 1"):
    related = {
        201: types.SimpleNamespace(Name=type_name),
        301: types.SimpleNamespace(Name=level_name),
    }
    element = types.SimpleNamespace(
        Id=_Id(element_id),
        UniqueId="anonymous-{}".format(element_id),
        Category=types.SimpleNamespace(Name=category),
        Document=_Document(related),
        LevelId=_Id(301),
        GetTypeId=lambda: _Id(201),
        get_BoundingBox=lambda view: types.SimpleNamespace(
            Min=types.SimpleNamespace(X=0.0, Y=0.0, Z=0.0),
            Max=types.SimpleNamespace(X=10.0, Y=5.0, Z=1.0),
        ),
    )
    return element


class PyRevitSelectionTests(unittest.TestCase):
    def test_current_selection_accepts_only_floor_roof_and_wall(self):
        elements = [
            _element("Floors", 101),
            _element("Roofs", 102),
            _element("Walls", 103),
            _element("Doors", 104),
        ]

        allowed, rejected_count = collect_allowed_elements(elements)

        self.assertEqual([item.Id.Value for item in allowed], [101, 102, 103])
        self.assertEqual(rejected_count, 1)

    def test_summary_exposes_identity_level_type_and_geometry(self):
        summary = summarize_element(_element("Floors"))

        self.assertEqual(summary["element_id"], "101")
        self.assertEqual(summary["category"], "Floors")
        self.assertEqual(summary["type_name"], "Generic")
        self.assertEqual(summary["level_name"], "Level 1")
        self.assertEqual(summary["bounding_box_mm"], [3048, 1524, 305])
        self.assertNotIn("Document", summary)

    def test_formatted_summary_is_readable_in_the_panel_and_chat(self):
        text = format_selection_summary(
            [summarize_element(_element("Walls", type_name="Exterior 200"))]
        )

        self.assertIn("已选择 1 个来源元素", text)
        self.assertIn("ID 101", text)
        self.assertIn("Walls", text)
        self.assertIn("Level 1", text)
        self.assertIn("Exterior 200", text)
        self.assertIn("3048 × 1524 × 305 mm", text)

    def test_highlight_changes_only_ui_selection_and_locates_elements(self):
        calls = []

        class _ListType(type):
            def __getitem__(cls, item):
                return list

        class _List(metaclass=_ListType):
            pass

        selection = types.SimpleNamespace(
            SetElementIds=lambda ids: calls.append(("select", list(ids)))
        )
        uidoc = types.SimpleNamespace(
            Selection=selection,
            ShowElements=lambda ids: calls.append(("show", list(ids))),
        )
        fake_revit_module = types.ModuleType("Autodesk.Revit")
        fake_revit_module.DB = types.SimpleNamespace(ElementId=_Id)
        fake_autodesk = types.ModuleType("Autodesk")
        fake_autodesk.Revit = fake_revit_module
        fake_generic = types.ModuleType("System.Collections.Generic")
        fake_generic.List = _List
        fake_pyrevit = types.ModuleType("pyrevit")
        elements = [_element("Floors", 101), _element("Walls", 102)]
        elements[1].Document = elements[0].Document
        fake_pyrevit.revit = types.SimpleNamespace(
            uidoc=uidoc, doc=elements[0].Document
        )

        with patch.dict(
            sys.modules,
            {
                "Autodesk": fake_autodesk,
                "Autodesk.Revit": fake_revit_module,
                "System.Collections.Generic": fake_generic,
                "pyrevit": fake_pyrevit,
            },
        ):
            highlight_elements(elements)

        self.assertEqual([name for name, _ in calls], ["select", "show"])
        self.assertEqual([item.Value for item in calls[0][1]], [101, 102])

    def test_highlight_refuses_elements_from_another_document(self):
        fake_pyrevit = types.ModuleType("pyrevit")
        fake_pyrevit.revit = types.SimpleNamespace(doc=_Document({}))

        with patch.dict(sys.modules, {"pyrevit": fake_pyrevit}):
            with self.assertRaisesRegex(RuntimeError, "another Revit document"):
                highlight_elements([_element("Floors", 101)])

    def test_cancel_is_a_distinct_safe_outcome(self):
        self.assertTrue(issubclass(SelectionCancelled, Exception))


if __name__ == "__main__":
    unittest.main()
