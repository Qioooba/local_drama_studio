"""Export the frozen Qwen-Image-2.1 graphs as both API and UI workflow JSON.

The delivery brief asks for the new T2I / single-reference / two-reference
workflows "as UI/API JSON, plus semantic bindings".  `work/workflow_packages/`
already holds the canonical server-side package (API graph + contract +
bindings) that the runtime actually executes, but an operator cannot drag that
into the ComfyUI frontend.  This exporter derives, from the *published* version,
the two human-facing representations:

``api/<CODE>.json``  the ``/prompt`` graph, exactly as executed
``ui/<CODE>.json``   the litegraph document the ComfyUI frontend loads

The UI document is derived, never hand-written, so it cannot drift from the
graph the Worker runs.  ``docs/qwen-image-2.1-workflows/README.md`` records the
semantic bindings and the frozen parameters.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT / "apps" / "api") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "apps" / "api"))

from local_drama.application.workflow_definitions import WorkflowDefinitionService  # noqa: E402
from local_drama.config import Settings  # noqa: E402
from local_drama.infrastructure.database.sqlite import Database  # noqa: E402

# Input types that ComfyUI renders as an inline widget rather than a socket.
_WIDGET_TYPES = frozenset({"INT", "FLOAT", "STRING", "BOOLEAN"})

_LINK_COLOURS = {
    "MODEL": "#B39DDB", "CLIP": "#FFD500", "VAE": "#FF6E6E", "CONDITIONING": "#FFA931",
    "LATENT": "#FF9CF9", "IMAGE": "#64B5F6", "MASK": "#81C784",
}


def _is_widget(definition: Any) -> bool:
    if not isinstance(definition, list) or not definition:
        return False
    kind = definition[0]
    if isinstance(kind, list) or kind == "COMBO":
        return True
    return str(kind) in _WIDGET_TYPES


def _socket_type(definition: Any) -> str:
    if not isinstance(definition, list) or not definition:
        return "*"
    kind = definition[0]
    if isinstance(kind, list) or kind == "COMBO":
        return "COMBO"
    return str(kind)


def _slot_definitions(schema: dict[str, Any]) -> dict[str, Any]:
    groups = (schema.get("input") or {}) if isinstance(schema, dict) else {}
    if not isinstance(groups, dict):
        return {}
    definitions: dict[str, Any] = {}
    for group in ("required", "optional"):
        values = groups.get(group)
        if isinstance(values, dict):
            definitions.update(values)
    return definitions


def _output_names(schema: dict[str, Any]) -> list[tuple[str, str]]:
    outputs = (schema.get("output") or []) if isinstance(schema, dict) else []
    names = (schema.get("output_name") or []) if isinstance(schema, dict) else []
    if isinstance(outputs, str):
        outputs = [outputs]
    if isinstance(names, str):
        names = [names]
    result: list[tuple[str, str]] = []
    for index, kind in enumerate(outputs):
        label = names[index] if index < len(names) and names[index] else (kind if isinstance(kind, str) else "OUTPUT")
        result.append((str(label), str(kind if isinstance(kind, str) else "COMBO")))
    return result


def to_ui(
    graph: dict[str, Any],
    object_info: dict[str, Any],
    *,
    title: str,
    description: str,
    api_graph: dict[str, Any],
) -> dict[str, Any]:
    """Convert an API graph into a litegraph document for the ComfyUI frontend."""

    order = sorted(graph, key=lambda key: int(key) if str(key).isdigit() else 0)
    nodes: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    link_id = 0
    # (source_node, source_slot) -> list of (target_node, target_slot)
    pending: list[tuple[str, int, str, int, str]] = []

    for position, node_id in enumerate(order):
        node = graph[node_id]
        class_type = str(node["class_type"])
        schema = object_info.get(class_type, {})
        definitions = _slot_definitions(schema)
        inputs = node.get("inputs", {})
        ui_inputs: list[dict[str, Any]] = []
        widgets: list[Any] = []
        for name, value in inputs.items():
            definition = definitions.get(name)
            if isinstance(value, list) and len(value) == 2 and str(value[0]) in graph:
                ui_inputs.append(
                    {
                        "name": name,
                        "type": _socket_type(definition),
                        "link": None,  # filled below once link ids are assigned
                        "_source": (str(value[0]), int(value[1])),
                    }
                )
            elif _is_widget(definition) or definition is None and not isinstance(value, (list, dict)):
                widgets.append(value)
            # A non-widget literal that is not a link (e.g. a model name typed
            # into a socket) is not representable in the UI document; record it
            # so the caller can fail loudly instead of emitting a silent lie.
            elif not (isinstance(value, list) and len(value) == 2):
                ui_inputs.append({"name": name, "type": _socket_type(definition), "link": None, "_literal": value})
        outputs = _output_names(schema)
        nodes.append(
            {
                "id": int(node_id) if str(node_id).isdigit() else position + 1,
                "_api_id": str(node_id),
                "type": class_type,
                "pos": [80 + (position % 6) * 330, 80 + (position // 6) * 260],
                "size": [300, 110],
                "flags": {},
                "order": position,
                "mode": 0,
                "inputs": ui_inputs,
                "outputs": [{"name": label, "type": kind, "links": []} for label, kind in outputs],
                "properties": {"Node name for S&R": class_type},
                "widgets_values": widgets,
            }
        )
        for slot, (source_node, source_slot) in enumerate(
            (item["_source"] for item in ui_inputs if "_source" in item)
        ):
            pending.append((source_node, source_slot, str(node_id), slot, ""))

    index = {str(item["_api_id"]): item for item in nodes}
    for source_node, source_slot, target_node, target_slot, _ in pending:
        link_id += 1
        source = index[source_node]
        target = index[target_node]
        source_output = source["outputs"][source_slot] if source_slot < len(source["outputs"]) else {"type": "*", "links": []}
        source_output.setdefault("links", []).append(link_id)
        target_input = target["inputs"][target_slot]
        target_input["link"] = link_id
        if not target_input.get("type") or target_input["type"] == "COMBO":
            target_input["type"] = source_output.get("type", "*")
        links.append(
            {
                "id": link_id,
                "origin_id": source["id"],
                "origin_slot": source_slot,
                "target_id": target["id"],
                "target_slot": target_slot,
                "type": source_output.get("type", "*"),
                "color": _LINK_COLOURS.get(str(source_output.get("type"))),
            }
        )

    for node in nodes:
        for item in node["inputs"]:
            item.pop("_source", None)
            if "_literal" in item:
                raise ValueError(
                    f"node {node['_api_id']} ({node['type']}) input {item['name']} holds a literal "
                    "that the ComfyUI frontend cannot represent; expected a widget or a link"
                )
        node.pop("_api_id", None)

    return {
        "id": None,
        "revision": 0,
        "last_node_id": max((node["id"] for node in nodes), default=0),
        "last_link_id": link_id,
        "nodes": nodes,
        "links": links,
        "groups": [],
        "config": {},
        "extra": {
            "ds": {"scale": 0.75, "offset": [40, 40]},
            "local_drama": {
                "title": title,
                "description": description,
                "note": "Derived from the published Local Drama Studio workflow version; do not hand-edit.",
                "api_graph": api_graph,
            },
        },
        "version": 0.4,
    }


def verify_ui(document: dict[str, Any]) -> list[str]:
    """Structural self-check: links must resolve to real slots."""

    problems: list[str] = []
    nodes = {node["id"]: node for node in document["nodes"]}
    referenced: set[int] = set()
    for link in document["links"]:
        origin = nodes.get(link["origin_id"])
        target = nodes.get(link["target_id"])
        if origin is None or target is None:
            problems.append(f"link {link['id']} references a missing node")
            continue
        if link["origin_slot"] >= len(origin["outputs"]):
            problems.append(f"link {link['id']} origin slot out of range")
        if link["target_slot"] >= len(target["inputs"]):
            problems.append(f"link {link['id']} target slot out of range")
        referenced.add(link["id"])
    for node in document["nodes"]:
        for item in node["inputs"]:
            if item.get("link") is not None and item["link"] not in referenced:
                problems.append(f"node {node['id']} input {item['name']} points at a missing link")
    return problems


def _object_info(base_url: str) -> dict[str, Any]:
    with urlopen(Request(f"{base_url}/object_info"), timeout=120) as response:  # noqa: S310 - loopback
        return json.loads(response.read().decode("utf-8"))


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pin", type=Path, default=_REPO_ROOT / "config" / "comfyui-qwen21-runtime.json")
    parser.add_argument("--base-url")
    parser.add_argument("--output-root", type=Path, default=_REPO_ROOT / "docs" / "qwen-image-2.1-workflows")
    parser.add_argument("--database", type=Path)
    args = parser.parse_args(argv)

    pin = json.loads(args.pin.read_text(encoding="utf-8"))
    base_url = args.base_url or f"http://{pin['host']}:{pin['port']}"
    object_info = _object_info(base_url)

    settings = Settings.from_env()
    if args.database:
        settings = settings.model_copy(update={"database_path": args.database})
    definitions = WorkflowDefinitionService(settings)
    database = Database(settings.database_path)

    summary: list[dict[str, Any]] = []
    failures: list[str] = []
    for row in database.connect().execute(
        """SELECT w.code, wv.id FROM workflow_versions wv JOIN workflows w ON w.id=wv.workflow_id
           WHERE w.code LIKE 'QWEN_IMAGE_21_%' AND wv.status='PUBLISHED' ORDER BY w.code"""
    ):
        code = str(row["code"])
        definition = definitions.get(code)
        compiled = definitions.instantiate(code, {})
        graph = compiled["workflow"]
        _write(args.output_root / "api" / f"{code}.json", graph)
        try:
            document = to_ui(graph, object_info, title=definition.title, description=definition.description, api_graph=graph)
        except ValueError as error:
            failures.append(f"{code}: {error}")
            continue
        problems = verify_ui(document)
        if problems:
            failures.extend(f"{code}: {problem}" for problem in problems)
            continue
        _write(args.output_root / "ui" / f"{code}.json", document)
        summary.append(
            {
                "definition_code": code,
                "capability": definition.capability,
                "workflow_version_id": str(row["id"]),
                "nodes": len(document["nodes"]),
                "links": len(document["links"]),
                "semantic_bindings": compiled["node_bindings"],
                "input_slots": compiled["contract"]["input_slots"],
            }
        )

    readme = _render_readme(summary)
    (args.output_root / "README.md").write_text(readme, encoding="utf-8")
    print(json.dumps({"exported": [item["definition_code"] for item in summary], "failures": failures}, ensure_ascii=False, indent=2))
    return 0 if summary and not failures else 2


def _render_readme(summary: list[dict[str, Any]]) -> str:
    lines = [
        "# Qwen-Image-2.1 工作流（UI / API JSON）",
        "",
        "本目录由 `scripts/qwen21/export_workflow_json.py` 从**已发布**的不可变工作流版本导出，",
        "不得手工编辑；`ui/` 与 `api/` 是同一条冻结图的两个表示。",
        "",
        "| definition | capability | 节点/连线 |",
        "|---|---|---|",
    ]
    for item in summary:
        lines.append(f"| `{item['definition_code']}` | `{item['capability']}` | {item['nodes']} / {item['links']} |")
    lines += ["", "## 语义绑定", ""]
    for item in summary:
        lines += [f"### `{item['definition_code']}`", "", "| 语义角色 | 节点 | 输入 |", "|---|---|---|"]
        for role, binding in sorted(item["semantic_bindings"].items()):
            lines.append(f"| `{role}` | `{binding['node_id']}` | `{binding['input']}` |")
        lines += ["", "输入槽：", ""]
        for role, slot in sorted(item["input_slots"].items()):
            lines.append(f"- `{role}` — required={slot.get('required', True)}")
        lines.append("")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
