"""Composite explainer creation contracts.

Reproduced defects on the audit snapshot (EXP-03, LDS-02, LDS-03, FE-A02):

* replaying the same request with the same ``Idempotency-Key`` returned
  ``400 INVALID_REQUEST`` because the project replayed and ``create_video`` then
  refused the already-existing video;
* a failure *after* the project commit (unknown ``channel_profile_id``) left an
  orphan project plus a consumed idempotency key, so the corrected retry got
  ``409 IDEMPOTENCY_KEY_CONFLICT`` — the user had neither a usable work nor a way
  to retry;
* the pasted manuscript was never persisted: only ``pasted_text_present`` and
  ``pasted_text_length`` reached ``input_payload_json``, and ``topic`` was set to
  the *title* because the browser sends ``topic = title`` for every non-topic input;
* the same title always derived the same project code, so an intentionally
  separate work with the same title could not be created.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from local_drama.application.explainers.commands import (
    EXPLAINER_CREATE_IDEMPOTENCY_SCOPE,
    ExplainerCreateCommand,
    ExplainerCreationService,
    build_explainer_creation_service,
    derive_project_code,
    normalise_pasted_text,
)
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database


def _command(**overrides: object) -> ExplainerCreateCommand:
    payload: dict[str, object] = {
        "title": "雨夜灯塔",
        "topic": "",
        "input_kind": "TOPIC",
        "target_seconds": 300,
        "source_locale": "zh-CN",
        "outputs": ({"edition_key": "main", "voice_locale": "zh-CN"},),
    }
    payload.update(overrides)
    return ExplainerCreateCommand(**payload)  # type: ignore[arg-type]


def _counts(database: Database) -> dict[str, int]:
    with database.connect() as connection:
        return {
            "projects": int(connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0]),
            "videos": int(connection.execute("SELECT COUNT(*) FROM explainer_videos").fetchone()[0]),
            "receipts": int(
                connection.execute(
                    "SELECT COUNT(*) FROM command_idempotencies WHERE scope=?",
                    (EXPLAINER_CREATE_IDEMPOTENCY_SCOPE,),
                ).fetchone()[0]
            ),
        }


def _service(database: Database, workspace: object) -> ExplainerCreationService:
    return build_explainer_creation_service(database, workspace)


# --------------------------------------------------------------------------- #
# idempotent replay
# --------------------------------------------------------------------------- #
def test_same_key_and_body_replays_the_same_project_and_video(database: Database, workspace: object) -> None:
    service = _service(database, workspace)
    command = _command()
    first = service.create_workspace(command, idempotency_key="key-1")
    second = service.create_workspace(command, idempotency_key="key-1")

    assert first["idempotent_replay"] is False
    assert second["idempotent_replay"] is True
    assert first["project"]["id"] == second["project"]["id"]
    assert first["video"]["id"] == second["video"]["id"]
    assert _counts(database) == {"projects": 1, "videos": 1, "receipts": 1}


def test_same_key_with_a_different_body_is_a_conflict_and_writes_nothing(
    database: Database, workspace: object
) -> None:
    service = _service(database, workspace)
    service.create_workspace(_command(), idempotency_key="key-2")
    with pytest.raises(DomainRuleError) as error:
        service.create_workspace(_command(topic="另一个主题"), idempotency_key="key-2")
    assert error.value.code == "IDEMPOTENCY_KEY_CONFLICT"
    assert _counts(database) == {"projects": 1, "videos": 1, "receipts": 1}


def test_changed_pasted_body_changes_the_request_digest(database: Database, workspace: object) -> None:
    service = _service(database, workspace)
    with pytest.raises(DomainRuleError) as error:
        service.create_workspace(_command(input_kind="PASTED_SCRIPT", pasted_text="第一版正文"), idempotency_key="k")
        service.create_workspace(
            _command(input_kind="PASTED_SCRIPT", pasted_text="第一版正文，但补了一句。"), idempotency_key="k"
        )
    assert error.value.code == "IDEMPOTENCY_KEY_CONFLICT"


def test_two_different_bodies_do_not_collide(database: Database, workspace: object) -> None:
    service = _service(database, workspace)
    first = service.create_workspace(_command(input_kind="PASTED_SCRIPT", pasted_text="甲正文"), idempotency_key="a")
    second = service.create_workspace(_command(input_kind="PASTED_SCRIPT", pasted_text="乙正文"), idempotency_key="b")
    assert first["project"]["id"] != second["project"]["id"]
    assert _counts(database)["videos"] == 2


# --------------------------------------------------------------------------- #
# atomicity
# --------------------------------------------------------------------------- #
def test_failed_video_validation_leaves_no_orphan_project(
    database: Database, workspace: object
) -> None:
    """An unknown channel profile used to leave the project behind."""

    service = _service(database, workspace)
    with pytest.raises(Exception):
        service.create_workspace(_command(channel_profile_id="does-not-exist"), idempotency_key="key-3")
    assert _counts(database) == {"projects": 0, "videos": 0, "receipts": 0}
    # The consumed key must not block the corrected retry either.
    corrected = service.create_workspace(_command(), idempotency_key="key-3")
    assert corrected["project"]["id"]
    assert _counts(database) == {"projects": 1, "videos": 1, "receipts": 1}


def test_failed_creation_removes_only_its_own_project_directory(
    database: Database, workspace: object
) -> None:
    service = _service(database, workspace)
    with pytest.raises(Exception):
        service.create_workspace(_command(channel_profile_id="does-not-exist"), idempotency_key="key-4")
    projects_root = Path(str(workspace.projects_root))
    if projects_root.exists():
        assert list(projects_root.iterdir()) == []


def test_intentional_second_work_with_the_same_title_is_allowed(
    database: Database, workspace: object
) -> None:
    service = _service(database, workspace)
    first = service.create_workspace(_command(), idempotency_key="new-1")
    second = service.create_workspace(_command(), idempotency_key="new-2")
    assert first["project"]["code"] != second["project"]["code"]
    assert first["project"]["id"] != second["project"]["id"]


def test_explicit_project_code_is_honoured_and_still_unique(
    database: Database, workspace: object
) -> None:
    service = _service(database, workspace)
    first = service.create_workspace(_command(project_code="my_code_1"), idempotency_key="c1")
    assert first["project"]["code"] == "my_code_1"
    with pytest.raises(DomainRuleError) as error:
        service.create_workspace(_command(project_code="my_code_1"), idempotency_key="c2")
    assert error.value.code == "PROJECT_CODE_EXISTS"


# --------------------------------------------------------------------------- #
# LDS-03: the pasted manuscript is persisted
# --------------------------------------------------------------------------- #
def test_pasted_manuscript_is_stored_and_readable_after_reconnect(
    database: Database, workspace: object
) -> None:
    service = _service(database, workspace)
    body = "第一段：灯塔在雨夜里重新亮起。\n第二段：林舟推开了旧仓库的门。"
    created = service.create_workspace(
        _command(title="雨夜灯塔", topic="雨夜灯塔", input_kind="PASTED_SCRIPT", pasted_text=body),
        idempotency_key="paste-1",
    )
    video_id = created["video"]["id"]

    # A brand-new connection and service instance: nothing is carried in memory.
    reopened = build_explainer_creation_service(Database(database.path), workspace)
    replay = reopened.create_workspace(
        _command(title="雨夜灯塔", topic="雨夜灯塔", input_kind="PASTED_SCRIPT", pasted_text=body),
        idempotency_key="paste-1",
    )
    assert replay["video"]["id"] == video_id
    payload = replay["video"]["input_payload_json"]
    assert payload["pasted_text"]["text"] == body
    assert payload["pasted_text"]["character_count"] == len(body)
    assert payload["pasted_text"]["content_sha256"]
    assert payload["input_kind"] == "PASTED_SCRIPT"


def test_same_length_different_body_has_a_different_content_hash(
    database: Database, workspace: object
) -> None:
    service = _service(database, workspace)
    first = service.create_workspace(
        _command(input_kind="PASTED_SCRIPT", pasted_text="甲甲甲"), idempotency_key="h1"
    )
    second = service.create_workspace(
        _command(input_kind="PASTED_SCRIPT", pasted_text="乙乙乙"), idempotency_key="h2"
    )
    first_hash = first["video"]["input_payload_json"]["pasted_text"]["content_sha256"]
    second_hash = second["video"]["input_payload_json"]["pasted_text"]["content_sha256"]
    assert first_hash != second_hash


def test_pasted_topic_is_never_silently_the_title(
    database: Database, workspace: object
) -> None:
    """The browser sends ``topic = title`` for non-topic input; the body wins."""

    service = _service(database, workspace)
    created = service.create_workspace(
        _command(
            title="标题不是正文",
            topic="标题不是正文",
            input_kind="PASTED_SCRIPT",
            pasted_text="真正的第一行正文\n第二行正文",
        ),
        idempotency_key="topic-1",
    )
    assert created["video"]["topic"] == "真正的第一行正文"


def test_pasted_text_normalisation_is_explicit(database: Database, workspace: object) -> None:
    service = _service(database, workspace)
    created = service.create_workspace(
        _command(input_kind="PASTED_SCRIPT", pasted_text="\ufeffA行\r\nB行\rC行"),
        idempotency_key="norm-1",
    )
    stored = created["video"]["input_payload_json"]["pasted_text"]["text"]
    assert stored == "A行\nB行\nC行"


def test_empty_pasted_text_is_stored_as_an_explicit_record(database: Database, workspace: object) -> None:
    service = _service(database, workspace)
    created = service.create_workspace(
        _command(input_kind="PASTED_SCRIPT", pasted_text=""), idempotency_key="empty-1"
    )
    record = created["video"]["input_payload_json"]["pasted_text"]
    assert record["character_count"] == 0
    assert record["text"] == ""


def test_input_payload_keeps_source_refs_and_reference_urls(database: Database, workspace: object) -> None:
    service = _service(database, workspace)
    created = service.create_workspace(
        _command(
            input_kind="REFERENCE_LINKS",
            source_refs=({"reference": "note.md", "role": "RESEARCH_SOURCE"},),
            reference_urls=("https://example.invalid/a",),
        ),
        idempotency_key="refs-1",
    )
    payload = created["video"]["input_payload_json"]
    assert payload["source_refs"] == [{"reference": "note.md", "role": "RESEARCH_SOURCE"}]
    assert payload["reference_urls"] == ["https://example.invalid/a"]
    assert payload["reference_url_count"] == 1


# --------------------------------------------------------------------------- #
# command identity helpers
# --------------------------------------------------------------------------- #
def test_derive_project_code_is_legal_and_request_scoped() -> None:
    import re

    code = derive_project_code("雨夜灯塔")
    assert re.fullmatch(r"[a-z][a-z0-9_]{1,63}", code)
    assert derive_project_code("雨夜灯塔", unique_suffix="a") != derive_project_code("雨夜灯塔", unique_suffix="b")


def test_project_code_prefix_stays_readable_for_ascii_titles() -> None:
    assert derive_project_code("The Lighthouse", unique_suffix="x").startswith("the_lighthouse_")


def test_reference_digest_ignores_nothing_that_changes_the_outcome() -> None:
    base = _command()
    variants = {
        "title": _command(title="另一个标题"),
        "topic": _command(topic="另一个主题"),
        "content_kind": _command(content_kind="ORIGINAL_FICTION"),
        "input_kind": _command(input_kind="DOCUMENT_IMPORT"),
        "target_seconds": _command(target_seconds=600),
        "tolerance": _command(tolerance_percent=1.0),
        "source_locale": _command(source_locale="en-US"),
        "automation": _command(automation_mode="MANUAL_REVIEW"),
        "inference": _command(inference_mode="ALLOW_CONFIGURED_CLOUD"),
        "research": _command(research_mode="WEB_RESEARCH"),
        "domains": _command(allowed_domains=("example.invalid",)),
        "aspect": _command(aspect_ratio="9:16"),
        "size": _command(width=1920, height=1080),
        "language": _command(primary_language="en-US"),
        "subtitle": _command(subtitle_mode="BURNED"),
        "fps": _command(fps_num=30),
        "outputs": _command(outputs=({"edition_key": "alt"},)),
        "pasted": _command(pasted_text="正文"),
    }
    digests = {name: variant.request_digest() for name, variant in variants.items()}
    digests["base"] = base.request_digest()
    assert len(set(digests.values())) == len(digests), digests


def test_normalise_pasted_text_rejects_a_non_string() -> None:
    with pytest.raises(DomainRuleError):
        normalise_pasted_text(None)  # type: ignore[arg-type]


def test_stored_receipt_contains_both_ids(database: Database, workspace: object) -> None:
    service = _service(database, workspace)
    created = service.create_workspace(_command(), idempotency_key="receipt-1")
    with database.connect() as connection:
        row = connection.execute(
            "SELECT response_json FROM command_idempotencies WHERE scope=? AND idempotency_key=?",
            (EXPLAINER_CREATE_IDEMPOTENCY_SCOPE, "receipt-1"),
        ).fetchone()
    stored = json.loads(str(row["response_json"]))
    assert stored["project_id"] == created["project"]["id"]
    assert stored["video_id"] == created["video"]["id"]
    assert stored["project_code"] == created["project"]["code"]


def test_interrupted_command_can_be_retried_with_the_same_key(
    database: Database, workspace: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash between the two writes must not poison the key."""

    service = _service(database, workspace)
    original = service.production.insert_video_in_transaction

    def explode(*args: object, **kwargs: object) -> dict[str, object]:
        raise RuntimeError("simulated crash after the project row")

    monkeypatch.setattr(service.production, "insert_video_in_transaction", explode)
    with pytest.raises(RuntimeError):
        service.create_workspace(_command(), idempotency_key="crash-1")
    assert _counts(database) == {"projects": 0, "videos": 0, "receipts": 0}

    monkeypatch.setattr(service.production, "insert_video_in_transaction", original)
    recovered = service.create_workspace(_command(), idempotency_key="crash-1")
    assert recovered["idempotent_replay"] is False
    assert _counts(database) == {"projects": 1, "videos": 1, "receipts": 1}


# --------------------------------------------------------------------------- #
# real HTTP contract
# --------------------------------------------------------------------------- #
def _http_client(workspace: object, monkeypatch: pytest.MonkeyPatch):
    from fastapi.testclient import TestClient

    from local_drama.main import create_app

    settings = workspace.model_copy(deep=True) if hasattr(workspace, "model_copy") else workspace
    settings.ensure_roots()
    from scripts.migrate import migrate

    migrate(settings.database_path)
    app = create_app(settings)
    client = TestClient(app)
    client.__enter__()
    return client


def _create_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "title": "雨夜灯塔",
        "topic": "",
        "content_kind": "FACTUAL_EXPLAINER",
        "input_kind": "PASTED_SCRIPT",
        "pasted_text": "第一段：灯塔在雨夜里重新亮起。\n第二段：林舟推开了旧仓库的门。",
        "target_seconds": 300,
        "source_locale": "zh-CN",
        "outputs": [{"edition_key": "main", "voice_locale": "zh-CN"}],
    }
    body.update(overrides)
    return body


def test_http_create_replays_idempotently(workspace: object, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _http_client(workspace, monkeypatch)
    try:
        body = _create_body()
        first = client.post("/api/v2/explainers", json=body, headers={"Idempotency-Key": "http-1"})
        assert first.status_code == 201, first.text
        payload = first.json()
        assert payload["idempotent_replay"] is False

        second = client.post("/api/v2/explainers", json=body, headers={"Idempotency-Key": "http-1"})
        assert second.status_code == 201, second.text
        assert second.json()["idempotent_replay"] is True
        assert second.json()["project"]["id"] == payload["project"]["id"]
        assert second.json()["video"]["id"] == payload["video"]["id"]

        conflict = client.post(
            "/api/v2/explainers",
            json=_create_body(topic="另一个主题"),
            headers={"Idempotency-Key": "http-1"},
        )
        assert conflict.status_code == 409, conflict.text
    finally:
        client.__exit__(None, None, None)


def test_http_create_persists_the_pasted_manuscript(workspace: object, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _http_client(workspace, monkeypatch)
    try:
        body = _create_body()
        created = client.post("/api/v2/explainers", json=body, headers={"Idempotency-Key": "http-2"})
        assert created.status_code == 201, created.text
        payload = created.json()
        # Re-read through a *fresh* GET: the manuscript must come back from
        # storage, not from the request that created it.
        overview = client.get(f"/api/v2/explainers/{payload['project']['id']}")
        assert overview.status_code == 200, overview.text
        candidates = [
            payload["video"].get("input_payload_json"),
            (overview.json().get("video") or {}).get("input_payload_json"),
            overview.json().get("video"),
        ]
        stored = next(
            (
                item
                for item in candidates
                if isinstance(item, dict) and isinstance(item.get("pasted_text"), dict)
            ),
            None,
        )
        assert stored is not None, list(overview.json().keys())
        assert stored["pasted_text"]["text"] == body["pasted_text"]
        assert stored["pasted_text"]["character_count"] == len(str(body["pasted_text"]))
        assert "PK" not in str(stored)
    finally:
        client.__exit__(None, None, None)
