"""Reusable G0 gate checks.

Two independent modes exist and they must never be confused:

* the **default portable mode** validates the versioned in-repo contract
  (``docs/release/g0-contract.json``) against the committed repository, so a
  clean checkout in any directory can run the gate; and
* the **extended blueprint audit**, which inspects an operator-supplied
  blueprint directory plus a real ``model_manifest.json`` outside the
  repository.  When those inputs are absent the audit reports
  ``NOT_CONFIGURED`` and points at the licensed UAT path instead of printing a
  fabricated PASS.

``scripts/g0_validate.py`` is the CLI; this module holds the logic so tests can
import it without spawning a process.  Nothing here imports application code,
writes files, or touches the network.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SPEC = REPO_ROOT / "docs" / "release" / "g0-contract.json"

PASS = "PASS"
FAIL = "FAIL"
NOT_CONFIGURED = "NOT_CONFIGURED"


class G0ValidationError(RuntimeError):
    """Raised when the contract specification itself is unusable."""


@dataclass(frozen=True)
class Check:
    """One named, independently reportable gate result."""

    code: str
    status: str
    detail: str
    findings: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return self.status == PASS


@dataclass(frozen=True)
class ValidationReport:
    """The full outcome of one validator run."""

    mode: str
    checks: tuple[Check, ...]

    @property
    def status(self) -> str:
        if any(check.status == FAIL for check in self.checks):
            return FAIL
        if any(check.status == NOT_CONFIGURED for check in self.checks):
            # An unconfigured optional audit is never reported as a PASS.
            return NOT_CONFIGURED
        return PASS

    @property
    def failures(self) -> tuple[str, ...]:
        return tuple(finding for check in self.checks if check.status == FAIL for finding in check.findings)


def _check(code: str, findings: list[str], detail: str) -> Check:
    return Check(code=code, status=FAIL if findings else PASS, detail=detail, findings=tuple(findings))


def load_spec(path: Path = DEFAULT_SPEC) -> dict[str, Any]:
    """Load and structurally validate the in-repo G0 contract specification."""

    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise G0ValidationError(f"cannot read the G0 contract specification {path}: {error}") from error
    if not isinstance(document, dict) or document.get("schema_version") != "localdrama.g0-contract.v1":
        raise G0ValidationError(f"{path} is not a localdrama.g0-contract.v1 specification")
    return document


def _resolve(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as error:
        raise G0ValidationError(f"contract path escapes the repository root: {relative}") from error
    return candidate


def check_components(root: Path, spec: dict[str, Any]) -> Check:
    """Every declared code/contract/migration/test/lock component must exist."""

    findings: list[str] = []
    for component in spec.get("required_components", []):
        code = str(component.get("code"))
        relative = str(component.get("path"))
        path = _resolve(root, relative)
        kind = str(component.get("kind", "file"))
        if kind == "directory":
            if not path.is_dir():
                findings.append(f"{code}: required directory is missing: {relative}")
                continue
        elif not path.is_file():
            findings.append(f"{code}: required file is missing: {relative}")
            continue
        for marker in component.get("markers", []):
            if path.is_file() and str(marker) not in path.read_text(encoding="utf-8", errors="replace"):
                findings.append(f"{code}: {relative} no longer contains the required marker {marker!r}")
    return _check("REQUIRED_COMPONENTS", findings, f"components={len(spec.get('required_components', []))}")


def check_schema_versions(root: Path, spec: dict[str, Any]) -> Check:
    """Declared versioned schemas must still carry their exact version literal."""

    findings: list[str] = []
    for entry in spec.get("required_schema_versions", []):
        code = str(entry.get("code"))
        path = _resolve(root, str(entry.get("path")))
        if not path.is_file():
            findings.append(f"{code}: schema file is missing: {entry.get('path')}")
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            findings.append(f"{code}: {entry.get('path')} is not valid JSON: {error}")
            continue
        observed = document.get(str(entry.get("field")))
        expected = entry.get("value")
        if observed != expected:
            findings.append(f"{code}: {entry.get('path')} {entry.get('field')} is {observed!r}, expected {expected!r}")
    return _check("SCHEMA_VERSIONS", findings, f"schemas={len(spec.get('required_schema_versions', []))}")


def _read_python_constant(path: Path, name: str) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    value = ast.literal_eval(node.value)
                    return str(value)
    raise G0ValidationError(f"{path} does not assign {name}")


def check_api_contract_version(root: Path, spec: dict[str, Any]) -> Check:
    """The declared API contract version must equal the one the code exports.

    Drift here means the committed OpenAPI/TS client describes a different
    compatibility boundary than the running application.
    """

    findings: list[str] = []
    source = spec.get("api_contract_version_source", {})
    source_path = _resolve(root, str(source.get("path")))
    if not source_path.is_file():
        findings.append(f"contract version source is missing: {source.get('path')}")
        return _check("API_CONTRACT_VERSION", findings, "source_missing")
    observed = _read_python_constant(source_path, str(source.get("constant")))
    declared = str(spec.get("api_contract_version"))
    if observed != declared:
        findings.append(f"{source.get('path')} exports {observed!r} but {DEFAULT_SPEC.name} declares {declared!r}")
    return _check("API_CONTRACT_VERSION", findings, f"api_contract_version={observed}")


def _generator_is_fresh(root: Path, spec: dict[str, Any]) -> tuple[bool, str]:
    """Re-run each declared generator in read-only ``--check`` mode.

    This is the only way to prove the committed OpenAPI snapshot and TS client
    still match the live application.  The generators are invoked as
    subprocesses so their application imports cannot perturb this process, and
    they must support ``--check`` (fail without writing).
    """

    generators = sorted({str(artifact.get("generator")) for artifact in spec.get("generated_artifacts", []) if artifact.get("generator")})
    for generator in generators:
        script = _resolve(root, generator)
        if not script.is_file():
            return False, f"declared generator is missing: {generator}"
        environment = dict(os.environ)
        environment["PYTHONPATH"] = os.pathsep.join(
            [str(root / "apps" / "api"), environment.get("PYTHONPATH", "")]
        ).rstrip(os.pathsep)
        completed = subprocess.run(
            [sys.executable, str(script), "--check"],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if completed.returncode != 0:
            output = (completed.stdout + completed.stderr).strip() or f"exit code {completed.returncode}"
            return False, f"{generator} --check failed: {output}"
    return True, f"generators={len(generators)}"


def check_generated_artifacts(root: Path, spec: dict[str, Any], *, verify_generator: bool = True) -> Check:
    """Committed generator output must exist and stay in sync with the source.

    Structural markers are always checked statically.  With
    ``verify_generator`` (the CLI default) each declared generator is also
    re-run read-only, which is the only proof that the snapshot is current.
    """

    findings: list[str] = []
    source = spec.get("api_contract_version_source", {})
    source_path = _resolve(root, str(source.get("path")))
    contract_version = _read_python_constant(source_path, str(source.get("constant"))) if source_path.is_file() else ""
    for artifact in spec.get("generated_artifacts", []):
        code = str(artifact.get("code"))
        relative = str(artifact.get("path"))
        path = _resolve(root, relative)
        if not path.is_file():
            findings.append(f"{code}: generated artifact is missing: {relative}; run {artifact.get('generator')}")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for marker in artifact.get("must_contain", []):
            if str(marker) not in text:
                findings.append(f"{code}: {relative} no longer contains {marker!r}; regenerate with {artifact.get('generator')}")
        if artifact.get("format") == "openapi":
            try:
                document = json.loads(text)
            except json.JSONDecodeError as error:
                findings.append(f"{code}: {relative} is not valid JSON: {error}")
                continue
            paths = document.get("paths", {})
            for marker in artifact.get("must_contain", []):
                if marker.startswith("/") and marker not in paths:
                    findings.append(f"{code}: {relative} does not document {marker!r}")
        if artifact.get("format") == "typescript" and contract_version:
            match = re.search(r"API_CONTRACT_VERSION\s*=\s*'([^']*)'", text)
            if match is None:
                findings.append(f"{code}: {relative} does not export a literal API_CONTRACT_VERSION")
            elif match.group(1) != contract_version:
                findings.append(
                    f"{code}: {relative} was generated for {match.group(1)!r} but the application exports {contract_version!r};"
                    f" re-run {artifact.get('generator')}"
                )
    detail = f"artifacts={len(spec.get('generated_artifacts', []))}"
    if verify_generator:
        fresh, generator_detail = _generator_is_fresh(root, spec)
        detail = f"{detail} {generator_detail}"
        if not fresh:
            findings.append(generator_detail)
    return _check("GENERATED_ARTIFACTS", findings, detail)


def read_migration_graph(root: Path, spec: dict[str, Any]) -> tuple[dict[str, str], dict[str, tuple[str, ...]]]:
    """Parse ``revision``/``down_revision`` from the alembic version scripts."""

    components = {str(item.get("code")): str(item.get("path")) for item in spec.get("required_components", [])}
    if "MIGRATIONS" not in components:
        raise G0ValidationError("the G0 contract specification declares no MIGRATIONS component")
    migrations = _resolve(root, components["MIGRATIONS"])
    revisions: dict[str, str] = {}
    parents: dict[str, tuple[str, ...]] = {}
    for path in sorted(migrations.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        revision: str | None = None
        raw_parents: object = None
        for node in tree.body:
            # Alembic's current template writes ``revision: str = "..."``
            # (an ``AnnAssign``); older revisions use a plain ``Assign``.  Both
            # spellings declare the same identifier and must be accepted, or the
            # validator reports a false "declares no revision identifier".
            if isinstance(node, ast.AnnAssign):
                if not isinstance(node.target, ast.Name) or node.value is None:
                    continue
                targets: list[ast.Name] = [node.target]
                value_node = node.value
            elif isinstance(node, ast.Assign):
                targets = [target for target in node.targets if isinstance(target, ast.Name)]
                value_node = node.value
            else:
                continue
            for target in targets:
                if target.id == "revision":
                    revision = str(ast.literal_eval(value_node))
                elif target.id == "down_revision":
                    raw_parents = ast.literal_eval(value_node)
        if revision is None:
            raise G0ValidationError(f"{path} declares no revision identifier")
        if isinstance(raw_parents, str):
            parents_for_revision = (raw_parents,)
        elif isinstance(raw_parents, (tuple, list)):
            parents_for_revision = tuple(str(item) for item in raw_parents)
        else:
            parents_for_revision = ()
        if revision in revisions:
            raise G0ValidationError(f"duplicate alembic revision {revision!r} in {path}")
        revisions[revision] = path.name
        parents[revision] = parents_for_revision
    return revisions, parents


def _reachable_heads(revisions: dict[str, str], parents: dict[str, tuple[str, ...]]) -> list[str]:
    referenced = {parent for values in parents.values() for parent in values}
    return sorted(revision for revision in revisions if revision not in referenced)


def check_migration_head(root: Path, spec: dict[str, Any]) -> Check:
    """The declared release head must be the single reachable alembic head."""

    findings: list[str] = []
    declared = list(spec.get("migration_heads", {}).get("expected_heads", []))
    contract_relative = str(spec.get("migration_heads", {}).get("source", ""))
    if contract_relative:
        contract_path = _resolve(root, contract_relative)
        if not contract_path.is_file():
            findings.append(f"release migration contract is missing: {contract_relative}")
        else:
            try:
                contract = json.loads(contract_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as error:
                findings.append(f"{contract_relative} is not valid JSON: {error}")
                contract = {}
            observed_heads = [str(item) for item in contract.get("expected_heads", [])]
            if observed_heads != declared:
                findings.append(
                    f"{contract_relative} declares expected_heads={observed_heads} but {DEFAULT_SPEC.name} declares {declared}"
                )
    revisions, parents = read_migration_graph(root, spec)
    if not revisions:
        findings.append("no alembic revision scripts were found")
        return _check("MIGRATION_HEAD", findings, "revisions=0")
    dangling = sorted(parent for values in parents.values() for parent in values if parent not in revisions)
    for parent in dangling:
        findings.append(f"alembic revision graph references a missing parent {parent!r}")
    heads = _reachable_heads(revisions, parents)
    if heads != sorted(declared):
        findings.append(f"reachable alembic heads are {heads} but the contract declares {sorted(declared)}")
    for head in declared:
        if head not in revisions:
            findings.append(f"declared release head {head!r} has no migration script")
            continue
        # Every revision must be reachable from the declared head, otherwise an
        # orphan branch would never run on upgrade.
        reachable: set[str] = set()
        frontier = [head]
        while frontier:
            current = frontier.pop()
            if current in reachable:
                continue
            reachable.add(current)
            frontier.extend(parents.get(current, ()))
        orphaned = sorted(set(revisions) - reachable)
        if orphaned:
            findings.append(f"{len(orphaned)} migration(s) are unreachable from {head!r}: {orphaned[:5]}")
    return _check("MIGRATION_HEAD", findings, f"revisions={len(revisions)} heads={heads}")


def check_quality_gates(root: Path, spec: dict[str, Any]) -> Check:
    """Declared test/static-analysis gates must still be configured."""

    findings: list[str] = []
    for gate in spec.get("quality_gates", []):
        code = str(gate.get("code"))
        relative = str(gate.get("path"))
        path = _resolve(root, relative)
        if not path.is_file():
            findings.append(f"{code}: declared gate file is missing: {relative}")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        key = str(gate.get("key"))
        expected = gate.get("value")
        if expected is not None and expected not in text:
            findings.append(f"{code}: {relative} no longer declares {key} = {expected!r}")
        contains = gate.get("contains")
        if contains is not None and str(contains) not in text:
            findings.append(f"{code}: {relative} no longer mentions {contains!r}")
    return _check("QUALITY_GATES", findings, f"gates={len(spec.get('quality_gates', []))}")


def validate_repository(
    root: Path = REPO_ROOT,
    spec_path: Path = DEFAULT_SPEC,
    *,
    verify_generator: bool = True,
) -> ValidationReport:
    """Run every portable in-repo gate.  Never requires anything outside ``root``."""

    spec = load_spec(spec_path)
    checks = (
        check_components(root, spec),
        check_schema_versions(root, spec),
        check_api_contract_version(root, spec),
        check_generated_artifacts(root, spec, verify_generator=verify_generator),
        check_migration_head(root, spec),
        check_quality_gates(root, spec),
    )
    return ValidationReport(mode="PORTABLE_IN_REPO", checks=checks)


# ---------------------------------------------------------------------------
# Opt-in external blueprint / model manifest audit
# ---------------------------------------------------------------------------


def requirement_ids(text: str, prefix: str) -> set[str]:
    return set(re.findall(rf"^\|\s*({re.escape(prefix)}[A-Z0-9]+-\d{{3}})\b", text, re.MULTILINE))


def _resolve_in_scope_documents(blueprint: Path, numbers: list[int]) -> list[Path]:
    paths: list[Path] = []
    for number in numbers:
        matches = sorted(blueprint.glob(f"{number:02d}_*.md"))
        if len(matches) != 1:
            raise G0ValidationError(f"expected exactly one in-scope document for {number:02d} under {blueprint}, observed {len(matches)}")
        paths.append(matches[0])
    return paths


def audit_blueprint(blueprint_root: Path | None, manifest_path: Path | None, spec_path: Path = DEFAULT_SPEC) -> ValidationReport:
    """Audit an operator-supplied blueprint directory and real model manifest.

    Returns a single ``NOT_CONFIGURED`` check (not a PASS) when either input is
    absent, so an unconfigured environment can never be mistaken for a verified
    external audit.
    """

    spec = load_spec(spec_path)
    audit_spec = spec["external_blueprint_audit"]
    if blueprint_root is None or manifest_path is None:
        missing = []
        if blueprint_root is None:
            missing.append(f"--blueprint-root ({audit_spec['blueprint_directory']})")
        if manifest_path is None:
            missing.append(f"--manifest ({audit_spec['manifest']})")
        return ValidationReport(
            mode="EXTENDED_BLUEPRINT_AUDIT",
            checks=(
                Check(
                    code="EXTERNAL_BLUEPRINT_AUDIT",
                    status=NOT_CONFIGURED,
                    detail=f"not configured: {', '.join(missing)} not supplied; UAT path: {audit_spec['uat_reference']}",
                ),
            ),
        )
    findings: list[str] = []
    detail_parts: list[str] = []
    if not blueprint_root.is_dir():
        findings.append(f"blueprint root does not exist: {blueprint_root}")
    if not manifest_path.is_file():
        findings.append(f"model manifest does not exist: {manifest_path}")
    if findings:
        return ValidationReport(
            mode="EXTENDED_BLUEPRINT_AUDIT",
            checks=(Check(code="EXTERNAL_BLUEPRINT_AUDIT", status=FAIL, detail="inputs unreadable", findings=tuple(findings)),),
        )
    documents = _resolve_in_scope_documents(blueprint_root, [int(item) for item in audit_spec["in_scope_documents"]])
    inventory = audit_spec["master_inventory"]
    requirements = (blueprint_root / inventory["requirements_document"]).read_text(encoding="utf-8")
    tests = (blueprint_root / inventory["tests_document"]).read_text(encoding="utf-8")
    fr_count = len(requirement_ids(requirements, "FR-"))
    nfr_count = len(requirement_ids(requirements, "NFR-"))
    tc_count = len(set(re.findall(r"\bTC-[A-Z]+-\d{3}\b", tests)))
    expected = (int(inventory["fr"]), int(inventory["nfr"]), int(inventory["tc"]))
    if (fr_count, nfr_count, tc_count) != expected:
        findings.append(f"master inventory drift: expected FR/NFR/TC={expected[0]}/{expected[1]}/{expected[2]}, observed {fr_count}/{nfr_count}/{tc_count}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    authoritative = manifest.get("authoritative_current_state", {})
    if not isinstance(authoritative, dict) or "route_status" not in authoritative:
        findings.append(f"{manifest_path} lacks authoritative_current_state.route_status")
    detail_parts.append(f"documents_in_scope={len(documents)}")
    detail_parts.append(f"manifest_version={manifest.get('manifest_version')}")
    detail_parts.append(f"fr_count={fr_count}")
    detail_parts.append(f"nfr_count={nfr_count}")
    detail_parts.append(f"tc_count={tc_count}")
    for path in documents:
        detail_parts.append(f"sha256[{path.name}]={hashlib.sha256(path.read_bytes()).hexdigest()}")
    return ValidationReport(
        mode="EXTENDED_BLUEPRINT_AUDIT",
        checks=(Check(code="EXTERNAL_BLUEPRINT_AUDIT", status=FAIL if findings else PASS, detail="; ".join(detail_parts), findings=tuple(findings)),),
    )
