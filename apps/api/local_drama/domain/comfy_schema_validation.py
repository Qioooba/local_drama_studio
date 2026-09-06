"""Validate a saved graph against the runtime's advertised input schema."""

from typing import Any

from local_drama.domain.image_input_roles import COMFY_IMAGE_INPUT_ROLES


def validate_comfy_inputs(
    graph: dict[str, Any], object_info: dict[str, Any], bindings: dict[str, Any],
) -> list[dict[str, Any]]:
    # These filenames are supplied only after project media is materialized by
    # the job runner. Model filenames remain literal and must exist now.
    deferred_images = {
        (str(binding.get("node_id")), str(binding.get("input")))
        for role, binding in bindings.items()
        if role in COMFY_IMAGE_INPUT_ROLES and isinstance(binding, dict)
    }
    errors: list[dict[str, Any]] = []
    for node_id, node in graph.items():
        if not isinstance(node, dict):
            continue
        class_type = str(node.get("class_type"))
        schema = object_info.get(class_type)
        groups = schema.get("input") if isinstance(schema, dict) else None
        if not isinstance(groups, dict):
            continue
        inputs = node.get("inputs", {})
        definitions: dict[str, Any] = {}
        dynamic_prefixes: list[str] = []
        for group in ("required", "optional", "hidden"):
            values = groups.get(group)
            if not isinstance(values, dict):
                continue
            definitions.update(values)
            for name, definition in values.items():
                if not isinstance(definition, list) or not definition:
                    continue
                if definition[0] == "COMFY_AUTOGROW_V3" and len(definition) > 1 and isinstance(definition[1], dict):
                    template = definition[1].get("template", {})
                    prefix = template.get("prefix") if isinstance(template, dict) else None
                    if isinstance(prefix, str) and prefix:
                        dynamic_prefixes.append(f"{name}.{prefix}")
        def report(name: str, error: str, **details: Any) -> None:
            errors.append({"node_id": str(node_id), "class_type": class_type, "input": name, "error": error, **details})

        required = groups.get("required", {})
        if isinstance(required, dict):
            for name, definition in required.items():
                autogrow = isinstance(definition, list) and definition and definition[0] == "COMFY_AUTOGROW_V3"
                if name not in inputs and not autogrow:
                    report(name, "REQUIRED_INPUT_MISSING")
        for name, value in inputs.items():
            if name not in definitions:
                if definitions and not any(name.startswith(prefix) for prefix in dynamic_prefixes):
                    report(name, "INPUT_NOT_DECLARED")
                continue
            definition = definitions[name]
            if not isinstance(definition, list) or not definition:
                continue
            # Node links are checked by graph validation and are not literals.
            if isinstance(value, list) and len(value) == 2 and str(value[0]) in graph and isinstance(value[1], int):
                continue
            if (str(node_id), name) in deferred_images:
                continue
            choices = definition[0]
            if choices == "COMBO" and len(definition) > 1 and isinstance(definition[1], dict):
                choices = definition[1].get("options")
            if isinstance(choices, list) and value not in choices:
                report(name, "VALUE_NOT_IN_LIST", value=value, allowed_values=choices)
    return errors
