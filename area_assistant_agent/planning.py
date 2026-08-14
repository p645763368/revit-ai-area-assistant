"""Read-only Revit planning loop with versioned architectural knowledge."""

import base64
from contextlib import nullcontext
from dataclasses import dataclass
import json
from pathlib import Path
import tempfile
from typing import Any, Callable, Dict, List, Optional
import uuid


READ_ONLY_QUERIES = {
    "overview": """return new {
    documentTitle = doc.Title,
    activeViewId = uidoc.ActiveView.Id.Value.ToString(),
    activeViewName = uidoc.ActiveView.Name,
    levelCount = new FilteredElementCollector(doc).OfClass(typeof(Level)).GetElementCount(),
    areaCount = new FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Areas).WhereElementIsNotElementType().GetElementCount()
};""",
    "levels": """return new FilteredElementCollector(doc)
    .OfClass(typeof(Level)).Cast<Level>()
    .OrderBy(x => x.Elevation)
    .Select(x => new { id = x.Id.Value.ToString(), uniqueId = x.UniqueId, name = x.Name, elevation = x.Elevation })
    .ToList();""",
    "area_schemes": """return new FilteredElementCollector(doc)
    .OfClass(typeof(AreaScheme)).Cast<AreaScheme>()
    .Select(x => new { id = x.Id.Value.ToString(), uniqueId = x.UniqueId, name = x.Name, isGrossBuildingArea = x.IsGrossBuildingArea })
    .ToList();""",
    "boundary_candidates": """var categories = new[] { BuiltInCategory.OST_Walls, BuiltInCategory.OST_Floors, BuiltInCategory.OST_Roofs };
return categories.SelectMany(category => new FilteredElementCollector(doc)
    .OfCategory(category).WhereElementIsNotElementType().ToElements())
    .Select(x => new {
        id = x.Id.Value.ToString(), uniqueId = x.UniqueId,
        category = x.Category == null ? null : x.Category.Name,
        name = x.Name,
        box = x.get_BoundingBox(null) == null ? null : new {
            min = new[] { x.get_BoundingBox(null).Min.X, x.get_BoundingBox(null).Min.Y, x.get_BoundingBox(null).Min.Z },
            max = new[] { x.get_BoundingBox(null).Max.X, x.get_BoundingBox(null).Max.Y, x.get_BoundingBox(null).Max.Z }
        }
    }).ToList();""",
}


TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "inspect_revit_model",
            "description": "Read a fixed, non-mutating Revit model summary.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "required": ["query"],
                "properties": {"query": {"enum": sorted(READ_ONLY_QUERIES)}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "capture_revit_view",
            "description": "Capture a Revit view as read-only visual evidence.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"view_id": {"type": ["integer", "null"]}},
            },
        },
    },
]


class KnowledgeCatalog:
    def __init__(self, root: Path):
        self.root = Path(root)

    def load(self) -> Dict[str, List[dict]]:
        return {
            "rules": self._load_group("rules"),
            "cases": self._load_group("cases"),
        }

    def _load_group(self, group: str) -> List[dict]:
        items = []
        for path in sorted((self.root / group).glob("*.json")):
            value = json.loads(path.read_text(encoding="utf-8"))
            if (
                not isinstance(value, dict)
                or not _nonempty(value.get("version"))
                or not _nonempty(value.get("provenance"))
                or not _nonempty(value.get("applicability"))
            ):
                raise ValueError("knowledge snapshot is missing version metadata: " + str(path))
            items.append(value)
        if not items:
            raise ValueError("no versioned knowledge snapshots found for " + group)
        return items


@dataclass(frozen=True)
class PlanningResult:
    summary: str
    question: str
    options: List[dict]

    @classmethod
    def from_dict(cls, value: dict) -> "PlanningResult":
        if not isinstance(value, dict) or set(value) != {"summary", "question", "options"}:
            raise ValueError("planning result has an incompatible shape")
        options = value.get("options")
        if not _nonempty(value.get("summary")) or not _nonempty(value.get("question")):
            raise ValueError("planning result text must not be empty")
        if not isinstance(options, list) or not 2 <= len(options) <= 4:
            raise ValueError("planning result must contain two to four options")
        identifiers = set()
        recommended = 0
        for option in options:
            if not isinstance(option, dict) or set(option) != {
                "id", "label", "recommended", "rationale", "impact"
            }:
                raise ValueError("planning option has an incompatible shape")
            if not all(_nonempty(option.get(key)) for key in ("id", "label", "rationale", "impact")):
                raise ValueError("planning option text must not be empty")
            if not isinstance(option.get("recommended"), bool):
                raise ValueError("planning recommendation flag must be boolean")
            identifiers.add(option["id"])
            recommended += int(option["recommended"])
        if len(identifiers) != len(options) or recommended != 1:
            raise ValueError("planning options require unique ids and one recommendation")
        return cls(value["summary"].strip(), value["question"].strip(), options)

    def as_dict(self) -> dict:
        return {"summary": self.summary, "question": self.question, "options": self.options}


@dataclass(frozen=True)
class ToolExecution:
    audit_output: dict
    model_output: dict
    image_data_url: Optional[str] = None


class ReadOnlyRevitTools:
    def __init__(self, client: Any, capture_directory: Path):
        self.client = client
        self.capture_directory = Path(capture_directory)

    def execute(self, name: str, arguments: dict) -> ToolExecution:
        if name == "inspect_revit_model":
            if set(arguments) != {"query"} or arguments.get("query") not in READ_ONLY_QUERIES:
                raise ValueError("read-only Revit query is not allowed")
            self._verify_single_target()
            result = self.client.call_tool(
                "revit_send_code_to_revit", {"code": READ_ONLY_QUERIES[arguments["query"]]}
            )
            if not result.get("executed"):
                raise RuntimeError("rvt-mcp read-only query failed")
            output = {"query": arguments["query"], "result": result.get("result")}
            return ToolExecution(output, output)
        if name == "capture_revit_view":
            if not isinstance(arguments, dict) or set(arguments) - {"view_id"}:
                raise ValueError("read-only screenshot arguments are invalid")
            view_id = arguments.get("view_id")
            if view_id is not None and (not isinstance(view_id, int) or isinstance(view_id, bool)):
                raise ValueError("read-only screenshot view id is invalid")
            self._verify_single_target()
            self.capture_directory.mkdir(parents=True, exist_ok=True)
            filename = "view-{}.png".format(uuid.uuid4().hex)
            stable_path = self.capture_directory / filename
            temporary_directory = Path(tempfile.gettempdir()) / "RevitAIAreaAssistant"
            temporary_directory.mkdir(parents=True, exist_ok=True)
            output_path = temporary_directory / filename
            payload = {"output_path": str(output_path), "pixel_size": 1600, "image_format": "png"}
            if view_id is not None:
                payload["view_id"] = view_id
            result = self.client.call_tool("capture_view_image", payload)
            saved_path = Path(result.get("saved_path", output_path))
            image_bytes = saved_path.read_bytes()
            stable_path.write_bytes(image_bytes)
            if saved_path != stable_path:
                try:
                    saved_path.unlink()
                except OSError:
                    pass
            audit = {"view_id": result.get("view_id", view_id), "saved_path": str(stable_path)}
            return ToolExecution(
                audit,
                {"view_id": audit["view_id"], "image_attached": True},
                "data:image/png;base64," + base64.b64encode(image_bytes).decode("ascii"),
            )
        raise ValueError("tool is outside the read-only Revit boundary")

    def _verify_single_target(self) -> None:
        discovery = self.client.call_tool("revit_list_available_targets", {})
        targets = discovery.get("targets", [])
        if discovery.get("count") != 1 or len(targets) != 1:
            raise RuntimeError("read-only planning requires exactly one Revit target")
        switched = self.client.call_tool(
            "revit_switch_target", {"version": str(targets[0]["year"]), "verify": True}
        )
        if not switched.get("ok") or not switched.get("verified"):
            raise RuntimeError("rvt-mcp target verification failed")


class PlanningAgent:
    def __init__(self, model_client: Any, knowledge: KnowledgeCatalog, mcp_client_factory: Callable[[], Any], max_turns: int = 6):
        self.model_client = model_client
        self.knowledge = knowledge
        self.mcp_client_factory = mcp_client_factory
        self.max_turns = max_turns

    def plan(self, conversation: List[dict], session_directory: Path, audit: Callable[[str, dict, Any, Optional[str]], None]) -> PlanningResult:
        knowledge = self.knowledge.load()
        messages = [{
            "role": "system",
            "content": (
                "You are the read-only planning stage of a Revit GFA assistant. "
                "Use tools whenever model evidence or a screenshot is needed. Screenshots are supporting evidence only; geometry queries remain authoritative. "
                "Never propose a final regulatory factor without user confirmation. Return only JSON with summary, question, and 2-4 options. "
                "Exactly one option must be recommended; every option needs id, label, recommended, rationale, and impact.\nKnowledge:\n"
                + json.dumps(knowledge, ensure_ascii=False)
            ),
        }] + list(conversation)
        candidate = self.mcp_client_factory()
        context = candidate if hasattr(candidate, "__enter__") else nullcontext(candidate)
        with context as client:
            tools = ReadOnlyRevitTools(client, Path(session_directory) / "screenshots")
            for _ in range(self.max_turns):
                turn = self.model_client.planning_turn(messages, TOOL_DEFINITIONS)
                calls = turn.get("tool_calls", [])
                if calls:
                    messages.append(
                        {
                            "role": "assistant",
                            "content": turn.get("content"),
                            "tool_calls": [
                                {
                                    "id": call["id"],
                                    "type": "function",
                                    "function": {
                                        "name": call["name"],
                                        "arguments": json.dumps(
                                            call["arguments"], ensure_ascii=False
                                        ),
                                    },
                                }
                                for call in calls
                            ],
                        }
                    )
                    image_data_urls = []
                    for call in calls:
                        name = call.get("name")
                        arguments = call.get("arguments")
                        if not isinstance(arguments, dict):
                            raise ValueError("model tool arguments must be an object")
                        try:
                            execution = tools.execute(name, arguments)
                            audit(name, arguments, execution.audit_output, None)
                        except Exception as error:
                            audit(name or "unknown", arguments, None, str(error))
                            raise
                        messages.append({
                            "role": "tool",
                            "tool_call_id": call.get("id"),
                            "content": json.dumps(execution.model_output, ensure_ascii=False),
                        })
                        if execution.image_data_url:
                            image_data_urls.append(execution.image_data_url)
                    for image_data_url in image_data_urls:
                        messages.append({
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": "Inspect this captured Revit view as supporting evidence."},
                                    {"type": "image_url", "image_url": {"url": image_data_url}},
                                ],
                            })
                    continue
                content = turn.get("content")
                if not isinstance(content, str):
                    raise ValueError("model did not return a planning result")
                return PlanningResult.from_dict(json.loads(content))
        raise RuntimeError("planning tool loop exceeded its turn limit")


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())
