from __future__ import annotations

import ast
import json

from scripts.audit_architecture_debt import MANIFEST_PATH, audit


def test_legacy_architecture_debt_does_not_grow() -> None:
    baseline = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    current = audit()
    signatures = {
        "concrete_database_dependencies": lambda entry: (entry["file"],),
        "cross_service_construction": lambda entry: (entry["file"], entry["service"]),
        "routes_without_response_model": lambda entry: (entry["file"], entry["operation_id"]),
    }
    for category, current_entries in current["categories"].items():
        signature = signatures[category]
        allowed = {signature(entry) for entry in baseline["categories"][category]}
        introduced = {signature(entry) for entry in current_entries} - allowed
        assert not introduced, f"new {category} debt must use a port/response schema: {sorted(introduced)}"


def test_the_port_factory_exemption_stays_narrow() -> None:
    """Only a named factory may wire a concrete service, not ordinary business logic.

    The exemption exists because a worker-side port factory has to name the service
    it binds; if it ever covered arbitrary methods the guard would stop protecting
    the application layer, so this asserts the scoping rule directly.
    """

    from scripts.audit_architecture_debt import _FACTORY_PREFIXES, _factory_scopes

    source = (
        "class Executor:\n"
        "    def tick(self):\n"
        "        return RealService(self.database)\n"
        "\n"
        "def build_ports(database):\n"
        "    return RealService(database)\n"
        "\n"
        "def business(database):\n"
        "    return RealService(database)\n"
    )
    tree = ast.parse(source)
    scopes = _factory_scopes(tree)
    calls = sorted(
        (node.lineno, node.func.id, scopes.get(id(node), ""))
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    )

    assert [(line, scope) for line, _name, scope in calls] == [
        (3, "tick"),
        (6, "build_ports"),
        (9, "business"),
    ]
    for line, scope in ((3, "tick"), (6, "build_ports"), (9, "business")):
        assert (scope.startswith(_FACTORY_PREFIXES)) is (line == 6)


def test_v2_product_context_has_explicit_contracts_and_no_database_dependency() -> None:
    current = audit()["categories"]
    assert not [entry for entry in current["concrete_database_dependencies"] if entry["file"].endswith("application/product_context.py")]
    assert not [entry for entry in current["routes_without_response_model"] if entry["file"].endswith("product_context_v2.py")]


def test_v2_shot_studio_has_explicit_contracts_and_no_database_dependency() -> None:
    current = audit()["categories"]
    assert not [entry for entry in current["concrete_database_dependencies"] if entry["file"].endswith("application/shot_studio.py")]
    assert not [entry for entry in current["routes_without_response_model"] if entry["file"].endswith("shot_studio_v2.py")]
