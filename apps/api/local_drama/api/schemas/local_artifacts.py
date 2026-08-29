from __future__ import annotations

from typing import Literal

from local_drama.api.schemas.common import LocalArtifactReference, StrictModel


class ProjectPackageExport(StrictModel):
    status: Literal["EXPORTED"]
    project_id: str
    artifact: LocalArtifactReference
    rel_path: str
    byte_size: int
    sha256: str
    entry_count: int
    expanded_bytes: int
    reused: bool
    database_mutated: Literal[False]
    runtime_contacted: Literal[False]
    network_contacted: Literal[False]


class ProjectPackageExportEnvelope(StrictModel):
    package: ProjectPackageExport


class ContactSheetExport(StrictModel):
    schema_version: Literal["localdrama.contact-sheet.v1"]
    status: Literal["EXPORTED"]
    artifact: LocalArtifactReference
    rel_path: str
    manifest_rel_path: str
    contact_sheet_rel_path: str
    export_hash: str
    item_count: int
    reused: bool
    database_mutated: Literal[False]
    runtime_contacted: Literal[False]
    network_contacted: Literal[False]


class ContactSheetExportEnvelope(StrictModel):
    export: ContactSheetExport
