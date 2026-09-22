"""Validate a saved graph against the runtime's advertised input schema."""

from typing import Any

from local_drama.domain.image_input_roles import COMFY_IMAGE_INPUT_ROLES


def _autogrow_names(name: str, definition: Any) -> tuple[set[str], list[str]]:
    """Expand one ``COMFY_AUTOGROW_V3`` input into its concrete child names.

    ComfyUI declares the children of an autogrow slot in one of two ways: an
    explicit ``names`` list (as ``TextEncodeQwenImage21`` uses for
    ``image_1``..``image_16``) or a ``prefix`` that the front-end expands.  Both
    must be understood here, otherwise a perfectly valid graph is reported as
    using undeclared inputs.
    """

    if not isinstance(definition, list) or not definition or definition[0] != "COMFY_AUTOGROW_V3":
        return set(), []
    if len(definition) < 2 or not isinstance(definition[1], dict):
        return set(), []
    template = definition[1].get("template")
    if not isinstance(template, dict):
        return set(), []
    exact: set[str] = set()
    names = template.get("names")
    if isinstance(names, list):
        exact.update(f"{name}.{item}" for item in names if isinstance(item, str) and item)
    prefixes: list[str] = []
    prefix = template.get("prefix")
    if isinstance(prefix, str) and prefix:
        prefixes.append(f"{name}.{prefix}")
    return exact, prefixes


def _same_choice(value: Any, choices: list[Any]) -> bool:
    """Compare a submitted value against an advertised combo list, exactly.

    This is deliberately *not* tolerant of path-separator differences: ComfyUI
    advertises model filenames with the host separator and rejects the other
    form at ``/prompt`` time.  Accepting ``a/b.safetensors`` here when the
    runtime offers ``a\\b.safetensors`` would turn a hard runtime failure into a
    passing local validation, which is the opposite of this check's purpose.
    """

    return value in choices


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
        dynamic_children: set[str] = set()
        dynamic_prefixes: list[str] = []
        for group in ("required", "optional", "hidden"):
            values = groups.get(group)
            if not isinstance(values, dict):
                continue
            definitions.update(values)
            for name, definition in values.items():
                exact, prefixes = _autogrow_names(str(name), definition)
                dynamic_children.update(exact)
                dynamic_prefixes.extend(prefixes)
        def report(
            name: str,
            error: str,
            *,
            _node_id: Any = node_id,
            _class_type: str = class_type,
            **details: Any,
        ) -> None:
            errors.append(
                {
                    "node_id": str(_node_id),
                    "class_type": _class_type,
                    "input": name,
                    "error": error,
                    **details,
                }
            )

        required = groups.get("required", {})
        if isinstance(required, dict):
            for name, definition in required.items():
                autogrow = isinstance(definition, list) and definition and definition[0] == "COMFY_AUTOGROW_V3"
                if name not in inputs and not autogrow:
                    report(name, "REQUIRED_INPUT_MISSING")
        for name, value in inputs.items():
            if name not in definitions:
                declared = name in dynamic_children or any(name.startswith(prefix) for prefix in dynamic_prefixes)
                if definitions and not declared:
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
            if isinstance(choices, list) and not _same_choice(value, choices):
                report(name, "VALUE_NOT_IN_LIST", value=value, allowed_values=choices)
    return errors
