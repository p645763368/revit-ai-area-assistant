# -*- coding: utf-8 -*-
"""Read, summarize, select, and locate Revit source elements without writes."""

from __future__ import unicode_literals


_FALLBACK_ALLOWED_CATEGORY_NAMES = frozenset(
    ("Floors", "Roofs", "Walls", "楼板", "屋顶", "墙")
)
_FEET_TO_MM = 304.8


class SelectionCancelled(Exception):
    """The user safely cancelled interactive Revit selection."""


def _id_value(element_id):
    value = getattr(element_id, "Value", None)
    if value is None:
        value = getattr(element_id, "IntegerValue", None)
    return value


def _runtime_allowed_category_ids():
    from Autodesk.Revit import DB

    return frozenset(
        int(item)
        for item in (
            DB.BuiltInCategory.OST_Floors,
            DB.BuiltInCategory.OST_Roofs,
            DB.BuiltInCategory.OST_Walls,
        )
    )


def is_allowed_element(element, allowed_category_ids=None):
    category = getattr(element, "Category", None)
    if category is None:
        return False
    if allowed_category_ids is not None:
        category_id = _id_value(category.Id)
        return category_id in allowed_category_ids
    return getattr(category, "Name", "") in _FALLBACK_ALLOWED_CATEGORY_NAMES


def collect_allowed_elements(elements, allowed_category_ids=None):
    allowed = []
    rejected_count = 0
    for element in elements:
        if is_allowed_element(element, allowed_category_ids):
            allowed.append(element)
        else:
            rejected_count += 1
    return allowed, rejected_count


def current_selection():
    from pyrevit import revit

    return collect_allowed_elements(
        list(revit.get_selection()),
        _runtime_allowed_category_ids(),
    )


def interactive_selection():
    import Autodesk.Revit.UI as UI
    from Autodesk.Revit.Exceptions import OperationCanceledException
    from pyrevit import revit

    allowed_ids = _runtime_allowed_category_ids()

    class AllowedSourceFilter(UI.Selection.ISelectionFilter):
        def AllowElement(self, element):
            return is_allowed_element(element, allowed_ids)

        def AllowReference(self, reference, position):
            return False

    try:
        references = revit.uidoc.Selection.PickObjects(
            UI.Selection.ObjectType.Element,
            AllowedSourceFilter(),
            "选择Floor、Roof或Wall；按Esc安全取消",
        )
    except OperationCanceledException:
        raise SelectionCancelled()
    return [revit.doc.GetElement(reference.ElementId) for reference in references]


def highlight_elements(elements):
    from pyrevit import revit

    for element in elements:
        document = getattr(element, "Document", None)
        if document is None or not document.Equals(revit.doc):
            raise RuntimeError("selected element belongs to another Revit document")

    from Autodesk.Revit import DB
    from System.Collections.Generic import List

    element_ids = List[DB.ElementId]([element.Id for element in elements])
    revit.uidoc.Selection.SetElementIds(element_ids)
    revit.uidoc.ShowElements(element_ids)


def _related_name(document, element_id, fallback):
    if element_id is None or _id_value(element_id) in (-1, None):
        return fallback
    related = document.GetElement(element_id)
    return getattr(related, "Name", fallback) if related is not None else fallback


def _bounding_box_mm(element):
    bounding_box = element.get_BoundingBox(None)
    if bounding_box is None:
        return None
    return [
        int(round(abs(maximum - minimum) * _FEET_TO_MM))
        for minimum, maximum in (
            (bounding_box.Min.X, bounding_box.Max.X),
            (bounding_box.Min.Y, bounding_box.Max.Y),
            (bounding_box.Min.Z, bounding_box.Max.Z),
        )
    ]


def summarize_element(element):
    document = element.Document
    return {
        "element_id": str(_id_value(element.Id)),
        "unique_id": getattr(element, "UniqueId", ""),
        "category": getattr(element.Category, "Name", "<unknown>"),
        "level_name": _related_name(
            document, getattr(element, "LevelId", None), "<无关联楼层>"
        ),
        "type_name": _related_name(document, element.GetTypeId(), "<无类型>"),
        "bounding_box_mm": _bounding_box_mm(element),
    }


def summarize_elements(elements):
    return [summarize_element(element) for element in elements]


def format_selection_summary(summaries):
    lines = ["已选择 {} 个来源元素".format(len(summaries))]
    for index, item in enumerate(summaries, 1):
        dimensions = item.get("bounding_box_mm")
        geometry = (
            "{} × {} × {} mm".format(*dimensions)
            if dimensions is not None
            else "无可见包围盒"
        )
        lines.append(
            "{}. ID {} | UID {} | {} | 楼层 {} | 类型 {} | 包围盒 {}".format(
                index,
                item["element_id"],
                item.get("unique_id", ""),
                item["category"],
                item["level_name"],
                item["type_name"],
                geometry,
            )
        )
    return "\n".join(lines)


def create_selection_executor(callback):
    """Create the one ExternalEvent gateway used by the modeless pane."""
    import Autodesk.Revit.UI as UI

    class SelectionExternalEventHandler(UI.IExternalEventHandler):
        def __init__(self):
            self.action = None
            self.elements = []

        def Execute(self, ui_application):
            action = self.action
            elements = self.elements
            self.action = None
            self.elements = []
            try:
                if action == "current":
                    selected, rejected = current_selection()
                    callback("selected", selected, rejected, None)
                elif action == "interactive":
                    callback("selected", interactive_selection(), 0, None)
                elif action == "highlight":
                    highlight_elements(elements)
                    callback("highlighted", elements, 0, None)
            except SelectionCancelled:
                callback("cancelled", [], 0, None)
            except Exception as exc:
                callback("error", [], 0, str(exc))

        def GetName(self):
            return "AI Area Assistant source selection"

    class SelectionExecutor(object):
        def __init__(self):
            self.handler = SelectionExternalEventHandler()
            self.external_event = UI.ExternalEvent.Create(self.handler)

        def request(self, action, elements=None):
            if self.handler.action is not None:
                raise RuntimeError("another Revit selection operation is pending")
            self.handler.action = action
            self.handler.elements = list(elements or [])
            self.external_event.Raise()

    return SelectionExecutor()
