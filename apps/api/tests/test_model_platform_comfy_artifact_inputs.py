from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from local_drama.domain.errors import DomainRuleError
from local_drama.model_platform.application.comfy_artifact_inputs import materialize_v2_comfy_artifact_inputs


class _Database:
    def __init__(self, row):
        self.row = row

    @contextmanager
    def connect(self):
        yield SimpleNamespace(execute=lambda *_args: SimpleNamespace(fetchone=lambda: self.row))


def test_v2_comfy_artifact_input_materializes_only_verified_image_v2_output(workspace) -> None:
    source = workspace.work_root / "v2" / "source.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"verified-v2-image")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    database = _Database({
        "id": "artifact-v2", "kind": "COMFY_OUTPUT", "status": "VERIFIED", "sandbox_rel_path": "v2/source.png",
        "sha256": digest, "source_job_type": "MODEL_PLATFORM_EXECUTION",
        "execution_binding_json": json.dumps({"expected_output": {"media_kind": "IMAGE"}}),
    })

    inputs = materialize_v2_comfy_artifact_inputs(
        database,  # type: ignore[arg-type]
        workspace,
        {"PROMPT": "雨夜的橘猫", "FIRST_FRAME": {"artifact_id": "artifact-v2"}},
    )

    assert inputs["PROMPT"] == "雨夜的橘猫"
    assert inputs["FIRST_FRAME"] == f"v2-artifact-v2-{digest[:12]}.png"
    assert (workspace.comfy_input_root / inputs["FIRST_FRAME"]).read_bytes() == source.read_bytes()


def test_v2_comfy_artifact_input_rejects_non_media_role_without_querying_database(workspace) -> None:
    with pytest.raises(DomainRuleError) as raised:
        materialize_v2_comfy_artifact_inputs(
            _Database(None),  # type: ignore[arg-type]
            workspace,
            {"PROMPT": {"artifact_id": "artifact-v2"}},
        )

    assert raised.value.code == "MP_COMFY_ARTIFACT_ROLE_UNSUPPORTED"


def test_v2_comfy_artifact_input_rejects_a_non_image_source_contract(workspace) -> None:
    database = _Database({
        "id": "artifact-v2", "kind": "COMFY_OUTPUT", "status": "VERIFIED", "sandbox_rel_path": "missing.mp4",
        "sha256": "a" * 64, "source_job_type": "MODEL_PLATFORM_EXECUTION",
        "execution_binding_json": json.dumps({"expected_output": {"media_kind": "VIDEO"}}),
    })

    with pytest.raises(DomainRuleError) as raised:
        materialize_v2_comfy_artifact_inputs(
            database,  # type: ignore[arg-type]
            workspace,
            {"FIRST_FRAME": {"artifact_id": "artifact-v2"}},
        )

    assert raised.value.code == "MP_COMFY_ARTIFACT_MEDIA_KIND_INVALID"
