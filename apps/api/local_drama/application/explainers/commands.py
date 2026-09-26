"""Composite explainer commands: one durable, replayable business operation.

The audit found that ``POST /explainers`` was two independent transactions ("create
the project", "create the video") wrapped in one HTTP handler.  Consequences, all
confirmed on the snapshot:

* a replay with the same ``Idempotency-Key`` replayed the *project* and then hit
  ``INVALID_REQUEST`` from ``create_video`` because the video already existed;
* a failure after the project commit (for example an unknown
  ``channel_profile_id``) left an orphan project plus a consumed idempotency key, so
  the corrected retry got ``IDEMPOTENCY_KEY_CONFLICT``;
* the same title always derived the same project code, so an intentionally
  separate second work of the same name could never be created.

This module owns the whole operation instead: a single write transaction that
inserts the project, the video and the composite idempotency receipt, plus the
staged directory ownership needed because a filesystem cannot join a SQLite
transaction.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import unicodedata
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from local_drama.application.explainers.sources import script_source_hash
from local_drama.application.ports.database import DatabaseUnitOfWork
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.explainers.contracts import ProductKind, content_hash
from local_drama.infrastructure.filesystem.template import build_project_tree

__all__ = [
    "EXPLAINER_CREATE_IDEMPOTENCY_SCOPE",
    "ExplainerCreateCommand",
    "ExplainerCreationService",
    "build_explainer_creation_service",
    "derive_project_code",
    "normalise_pasted_text",
    "pasted_text_record",
]

#: Idempotency scope for the *whole* explainer creation operation.  The narrower
#: ``project:create`` scope is still written by ``ProjectService`` for its own API,
#: but the explainer route now replays against this one.
EXPLAINER_CREATE_IDEMPOTENCY_SCOPE = "explainer:create"

#: Guard rails for a pasted manuscript.  The text is stored verbatim (normalised),
#: so the only limits are memory bounds, not content policy.
MAX_PASTED_CHARACTERS = 2_000_000
MAX_REFERENCE_URLS = 200


def derive_project_code(title: str, *, unique_suffix: str | None = None) -> str:
    """Derive a legal, *unique* project code from a title.

    Project codes must be 2–64 lowercase ASCII characters starting with a letter.
    A deterministic title hash alone makes two intentionally separate works with
    the same title collide, which is what the previous behaviour did; the suffix is
    therefore derived from the request identity (or a fresh UUID) and the title only
    supplies the readable stem.
    """

    normalized = unicodedata.normalize("NFKD", title or "")
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    stem = "".join(
        character if character.isalnum() else "_" for character in ascii_only.lower()
    ).strip("_")[:38]
    if not stem or not stem[0].isalpha():
        stem = f"explainer_{stem}" if stem else "explainer"
    suffix_source = unique_suffix or uuid.uuid4().hex
    digest = hashlib.sha1(f"{title}\x00{suffix_source}".encode("utf-8")).hexdigest()[:10]
    code = f"{stem[:48]}_{digest}"
    return code[:64]


def normalise_pasted_text(text: str) -> str:
    """Normalise a pasted manuscript exactly once, and never truncate silently.

    Newlines become ``LF`` and a leading BOM is dropped, matching the document
    importer's canonical form, so the stored body hash is stable across platforms.
    The text is never trimmed of its internal content and never silently cut short.
    """

    if not isinstance(text, str):
        raise DomainRuleError("SCHEMA_INVALID", "粘贴正文必须是字符串")
    normalised = text.replace("\r\n", "\n").replace("\r", "\n")
    if normalised.startswith("\ufeff"):
        normalised = normalised[1:]
    if len(normalised) > MAX_PASTED_CHARACTERS:
        raise DomainRuleError(
            "PASTED_TEXT_TOO_LARGE",
            "粘贴正文超过长度上限，请改用文件导入",
            {"characters": len(normalised), "max_characters": MAX_PASTED_CHARACTERS},
        )
    return normalised


def pasted_text_record(text: str, *, locale: str | None = None) -> dict[str, Any]:
    """The immutable record of one pasted manuscript.

    The body itself is stored: the previous projection kept only
    ``pasted_text_present`` and ``pasted_text_length``, so the manuscript was
    unrecoverable after creation and a request hash could not stand in for it.
    """

    normalised = normalise_pasted_text(text)
    digest = hashlib.sha256(normalised.encode("utf-8")).hexdigest()
    return {
        "kind": "PASTED_TEXT",
        "content_sha256": digest,
        "character_count": len(normalised),
        "non_whitespace_character_count": sum(1 for char in normalised if not char.isspace()),
        "line_count": len(normalised.splitlines()),
        "locale": locale,
        "text": normalised,
    }


@dataclass(frozen=True)
class ExplainerCreateCommand:
    """The complete, already-validated input of one explainer creation.

    Every field that changes the outcome is part of the command, because the
    command's digest *is* the idempotency identity: a different manuscript, output
    set or research policy must not replay the previous result.
    """

    title: str
    topic: str = ""
    content_kind: str = "FACTUAL_EXPLAINER"
    project_code: str | None = None
    input_kind: str = "TOPIC"
    #: Content-processing policy (spec §C1), orthogonal to ``input_kind``.  The
    #: channel (paste/file/link) says how the content arrived; the policy says what
    #: the AI may do with it.  ``None`` keeps a legacy client working and resolves
    #: to ``ADAPT_SOURCES``, which is what every pre-existing row meant.
    script_policy: str | None = None
    source_refs: tuple[Mapping[str, Any], ...] = ()
    reference_urls: tuple[str, ...] = ()
    pasted_text: str | None = None
    duration_mode: str = "TARGET"
    target_seconds: int = 300
    tolerance_percent: float = 5.0
    source_locale: str = "zh-CN"
    automation_mode: str = "AUTO_WITH_EXCEPTIONS"
    inference_mode: str = "LOCAL_ONLY"
    research_mode: str = "OFFLINE_IMPORT"
    allowed_domains: tuple[str, ...] = ()
    channel_profile_id: str | None = None
    channel_profile_version_id: str | None = None
    aspect_ratio: str | None = None
    width: int | None = None
    height: int | None = None
    primary_language: str | None = None
    subtitle_mode: str | None = None
    subtitle_language: str | None = None
    fps_num: int = 25
    fps_den: int = 1
    outputs: tuple[Mapping[str, Any], ...] = ()
    actor: str = "local-user"

    def normalised_payload(self) -> dict[str, Any]:
        """Canonical, JSON-safe projection used for the request digest."""

        return {
            "title": self.title,
            "topic": self.topic,
            "content_kind": self.content_kind,
            "project_code": self.project_code,
            "input_kind": self.input_kind,
            "script_policy": self.resolved_script_policy(),
            "source_refs": [dict(item) for item in self.source_refs],
            "reference_urls": list(self.reference_urls),
            "pasted_text_sha256": (
                None
                if self.pasted_text is None
                else hashlib.sha256(normalise_pasted_text(self.pasted_text).encode("utf-8")).hexdigest()
            ),
            "pasted_text_length": 0 if self.pasted_text is None else len(normalise_pasted_text(self.pasted_text)),
            "duration_mode": self.duration_mode,
            "target_seconds": int(self.target_seconds),
            "tolerance_percent": float(self.tolerance_percent),
            "source_locale": self.source_locale,
            "automation_mode": self.automation_mode,
            "inference_mode": self.inference_mode,
            "research_mode": self.research_mode,
            "allowed_domains": list(self.allowed_domains),
            "channel_profile_id": self.channel_profile_id,
            "channel_profile_version_id": self.channel_profile_version_id,
            "aspect_ratio": self.aspect_ratio,
            "width": self.width,
            "height": self.height,
            "primary_language": self.primary_language,
            "subtitle_mode": self.subtitle_mode,
            "subtitle_language": self.subtitle_language,
            "fps": {"num": int(self.fps_num), "den": int(self.fps_den)},
            "outputs": [dict(item) for item in self.outputs],
        }

    def request_digest(self) -> str:
        return content_hash(self.normalised_payload())

    def input_payload(self) -> dict[str, Any]:
        """The durable input projection written to ``explainer_videos``.

        A pasted manuscript is stored as an immutable record with its own content
        hash, so "which text was this work created from" is answerable after a
        restart without the browser.

        ``script_policy`` lives here rather than in a new table (spec §C1).  For
        ``PRESERVE_ORIGINAL`` the *exact* manuscript is additionally recorded
        under ``script_source_text`` together with its hash: only CRLF/CR fold to
        LF, so no ``strip()`` and no punctuation change can desynchronise the
        preserved slices from the stored revision (§C2 item 5).
        """

        policy = self.resolved_script_policy()
        payload: dict[str, Any] = {
            "source_refs": [dict(item) for item in self.source_refs],
            "reference_urls": list(self.reference_urls),
            "reference_url_count": len(self.reference_urls),
            "input_kind": self.input_kind,
            "script_policy": policy,
            "normalisation": "unicode_nfkc_newlines_lf" if self.pasted_text is not None else None,
        }
        if self.pasted_text is not None:
            payload["pasted_text"] = pasted_text_record(self.pasted_text, locale=self.source_locale)
        else:
            payload["pasted_text"] = None
        if policy == "PRESERVE_ORIGINAL" and self.pasted_text is not None:
            from local_drama.application.explainers.sources import (
                canonical_script_source_text,
                script_source_hash,
            )

            exact = canonical_script_source_text(self.pasted_text)
            payload["preserved_original"] = {
                "script_source_text": exact,
                "script_source_hash": script_source_hash(exact),
                "character_count": len(exact),
                "normalisation": "newlines_lf_only",
                "whitespace_preserved": True,
            }
        return payload

    def resolved_script_policy(self) -> str:
        """The definite processing policy of this command (legacy ⇒ ADAPT)."""

        from local_drama.application.explainers.contracts_v2 import resolve_script_policy

        return resolve_script_policy(self.script_policy)

    def preserved_source_text(self) -> str | None:
        """The exact manuscript when this command declares preserved mode."""

        if self.resolved_script_policy() != "PRESERVE_ORIGINAL" or self.pasted_text is None:
            return None
        from local_drama.application.explainers.sources import canonical_script_source_text

        return canonical_script_source_text(self.pasted_text)

    def resolved_topic(self) -> str:
        """The research topic, without ever substituting the title for the body.

        The previous route used ``topic or pasted_text or ""`` while the browser
        also sent ``topic = title`` for every non-topic input, so a pasted script
        became a video whose topic was its own title and whose body existed nowhere.
        """

        if self.input_kind == "TOPIC":
            return self.topic or self.title
        explicit = (self.topic or "").strip()
        if explicit and explicit != (self.title or "").strip():
            return explicit
        if self.pasted_text:
            normalised = normalise_pasted_text(self.pasted_text)
            first_line = next((line.strip() for line in normalised.splitlines() if line.strip()), "")
            return first_line[:120] or self.title
        return self.title


@dataclass
class _CreateOutcome:
    project_id: str
    video_id: str
    replayed: bool
    created_root: Path | None = None
    response: dict[str, Any] = field(default_factory=dict)


class ExplainerCreationService:
    """The single composite creation command for an explainer workspace."""

    def __init__(
        self,
        database: DatabaseUnitOfWork,
        settings: Any,
        *,
        project_service: Any | None = None,
        production_service: Any | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        # Dependencies are injected.  Building the concrete collaborators here would
        # add new cross-service construction to the application layer, which the
        # architecture-debt guard refuses; ``build_explainer_creation_service`` is the
        # single composition point that supplies the defaults.
        self.projects = project_service
        self.production = production_service

    def _require_dependencies(self) -> tuple[Any, Any]:
        if self.projects is None or self.production is None:
            raise DomainRuleError(
                "EXPLAINER_CREATION_DEPENDENCIES_MISSING",
                "解说创建服务缺少 project/production 依赖，请通过组合根构建",
            )
        return self.projects, self.production

    # ------------------------------------------------------------------ public
    def create_workspace(
        self,
        command: ExplainerCreateCommand,
        *,
        idempotency_key: str | None = None,
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        """Create project + video + inputs as one replable business operation."""

        request_digest = command.request_digest()
        project_code = command.project_code or derive_project_code(
            command.title, unique_suffix=idempotency_key or operation_id
        )
        if idempotency_key:
            replay = self._replay(idempotency_key, request_digest, project_code)
            if replay is not None:
                return replay

        created_root: Path | None = None
        project_id: str | None = None
        projects, production = self._require_dependencies()
        try:
            with self.database.transaction() as connection:
                project_id = projects._insert_project_in_connection(
                    connection,
                    code=project_code,
                    title=command.title,
                    episode_count=0,
                    season_count=0,
                    aspect_ratio=command.aspect_ratio,
                    fps_num=command.fps_num,
                    fps_den=command.fps_den,
                    target_duration_ms=int(command.target_seconds) * 1000,
                    allow_unconfigured_capabilities=True,
                    width=command.width,
                    height=command.height,
                    primary_language=command.primary_language or command.source_locale,
                    subtitle_mode=command.subtitle_mode,
                    subtitle_language=command.subtitle_language,
                    product_kind=ProductKind.EXPLAINER.value,
                )
                created_root = self._claim_project_root(connection, project_id, project_code, command.title)
                video = production.insert_video_in_transaction(
                    connection,
                    project_id=project_id,
                    title=command.title,
                    topic=command.resolved_topic(),
                    content_kind=command.content_kind,
                    input_kind=command.input_kind,
                    input_payload=command.input_payload(),
                    duration_mode=command.duration_mode,
                    target_seconds=command.target_seconds,
                    tolerance_percent=command.tolerance_percent,
                    source_locale=command.source_locale,
                    automation_mode=command.automation_mode,
                    inference_mode=command.inference_mode,
                    research_mode=command.research_mode,
                    allowed_domains=command.allowed_domains,
                    channel_profile_id=command.channel_profile_id,
                    channel_profile_version_id=command.channel_profile_version_id,
                    actor=command.actor,
                    require_no_existing_video=False,
                )
                response = {
                    "project_id": str(project_id),
                    "video_id": str(video["id"]),
                    "project_code": project_code,
                    "request_digest": request_digest,
                    "operation_id": operation_id or str(uuid.uuid4()),
                }
                # Audit A4 / §B2.1: "已有口播稿" must be usable immediately.  The
                # preflight gate refuses a pasted-script work without a script
                # revision, so the exact manuscript is registered deterministically
                # *inside this same transaction*, before any plan can demand it.
                preserved = self._register_preserved_script_in_transaction(
                    connection,
                    command=command,
                    project_id=str(project_id),
                    video_id=str(video["id"]),
                    project_code=project_code,
                )
                if preserved is not None:
                    response["preserved_script_revision_id"] = preserved["script_revision_id"]
                    response["preserved_script_source_hash"] = preserved["script_source_hash"]
                if idempotency_key:
                    connection.execute(
                        "INSERT INTO command_idempotencies "
                        "(scope,idempotency_key,payload_hash,response_json) VALUES (?,?,?,?)",
                        (
                            EXPLAINER_CREATE_IDEMPOTENCY_SCOPE,
                            idempotency_key.strip(),
                            request_digest,
                            json.dumps(response, ensure_ascii=False, sort_keys=True),
                        ),
                    )
        except Exception:
            # A filesystem cannot join the SQLite transaction, so the directory is
            # removed explicitly — and only the one this command created.
            if created_root is not None:
                projects._discard_owned_project_root(created_root, str(project_id))
            raise

        return {
            **self._read_back(str(project_id), str(response["video_id"])),
            "idempotent_replay": False,
            "operation_id": response["operation_id"],
            "request_digest": request_digest,
            "preserved_script_revision_id": response.get("preserved_script_revision_id"),
            "preserved_script_source_hash": response.get("preserved_script_source_hash"),
        }

    # ----------------------------------------------------------------- private
    def _register_preserved_script_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        command: ExplainerCreateCommand,
        project_id: str,
        video_id: str,
        project_code: str,
    ) -> dict[str, Any] | None:
        """Register the exact pasted manuscript as a preserved script revision.

        Returns ``None`` when this command is not a preserved-mode paste.  The
        registration reuses the narration service's own authority
        (``create_script_revision``/``freeze_script``); it never writes the
        narration tables directly, so the preserved body goes through exactly the
        same equivalence proof as every other revision.
        """

        source_text = command.preserved_source_text()
        if not source_text:
            return None
        # Port-style factory: the concrete service is named in ``build_*``, which
        # is the one place the architecture guard allows it.
        from local_drama.application.explainers.narration import build_narration_service
        from local_drama.infrastructure.database.explainer_repository import ExplainerRepository

        service = build_narration_service(ExplainerRepository(connection))
        script_source_file = self._write_script_source_file(
            source_text,
            script_source_hash=script_source_hash(source_text),
            project_code=project_code,
        )
        registered = service.register_preserved_script(
            project_id=project_id,
            video_id=video_id,
            locale=command.source_locale,
            title=command.title,
            script_source_text=source_text,
            actor=command.actor,
            freeze=True,
            extra_provenance={
                "registered_at": "EXPLAINER_CREATE",
                "input_kind": command.input_kind,
                "content_sha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
                "script_source_file": script_source_file,
            },
        )
        revision = registered["script_revision"]
        return {
            "script_revision_id": str(revision["id"]),
            "script_source_hash": registered["script_source_hash"],
            "segment_count": len(registered["segments"]),
            "frozen": bool(registered["frozen"]),
            "validation": registered["validation"],
            "script_source_file": script_source_file,
        }

    def _write_script_source_file(
        self, source_text: str, *, script_source_hash: str, project_code: str
    ) -> str | None:
        """Keep the exact preserved manuscript as a UTF-8 file (§C2 item 5).

        The content hash is the file's name stem, so the stored file and the
        recorded hash cannot drift apart, and the text is written byte-for-byte
        (no BOM, no added newline).  A filesystem that cannot be written is
        reported as ``None`` instead of failing the whole creation: the exact text
        is also stored in the input projection and in the revision provenance.
        """

        try:
            project_root = Path(self.settings.projects_root) / project_code
            directory = project_root / "01_story" / "source_documents"
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / f"{script_source_hash}.script-source.txt"
            if not target.is_file():
                target.write_text(source_text, encoding="utf-8", newline="")
            return target.relative_to(project_root).as_posix()
        except OSError:
            return None

    def _replay(
        self, idempotency_key: str, request_digest: str, project_code: str
    ) -> dict[str, Any] | None:
        key = str(idempotency_key).strip()
        if not key:
            return None
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT payload_hash,response_json FROM command_idempotencies WHERE scope=? AND idempotency_key=?",
                (EXPLAINER_CREATE_IDEMPOTENCY_SCOPE, key),
            ).fetchone()
            if row is None:
                return None
            if str(row["payload_hash"]) != request_digest:
                raise DomainRuleError(
                    "IDEMPOTENCY_KEY_CONFLICT",
                    "相同 Idempotency-Key 已用于不同的解说创建请求",
                    {"project_code": project_code},
                )
            stored = json.loads(str(row["response_json"]))
            project_id = str(stored.get("project_id") or "")
            video_id = str(stored.get("video_id") or "")
            if not project_id or not video_id:
                raise DomainRuleError(
                    "IDEMPOTENCY_KEY_CONFLICT", "幂等记录与实际解说作品不一致", {"project_code": project_code}
                )
            existing_video = connection.execute(
                "SELECT id FROM explainer_videos WHERE id = ? AND project_id = ?",
                (video_id, project_id),
            ).fetchone()
            if existing_video is None:
                raise DomainRuleError(
                    "IDEMPOTENCY_KEY_CONFLICT",
                    "幂等记录指向的解说作品已不存在，请用新的操作键重新创建",
                    {"project_code": project_code, "project_id": project_id},
                )
            from local_drama.infrastructure.database.explainer_repository import ExplainerRepository

            preserved = ExplainerRepository(connection).preserved_script_revision(video_id)
        return {
            **self._read_back(project_id, video_id),
            "idempotent_replay": True,
            "operation_id": stored.get("operation_id"),
            "request_digest": request_digest,
            "preserved_script_revision_id": str(preserved["id"]) if preserved else None,
            "preserved_script_source_hash": (
                str((preserved.get("provenance_json") or {}).get("script_source_hash"))
                if preserved
                else None
            ),
        }

    def _claim_project_root(
        self,
        connection: sqlite3.Connection,
        project_id: str,
        code: str,
        title: str,
    ) -> Path:
        """Stage the project directory and record its ownership marker.

        ``build_project_tree`` writes a ``project.json`` ownership marker, so the
        failure path can prove the directory belongs to this command before it is
        removed.  The row is already inserted at this point, which is why the
        directory is claimed inside the same transaction.
        """

        try:
            root, _ = build_project_tree(
                self.settings.projects_root,
                project_id,
                code,
                title,
                episode_count=0,
                season_count=0,
            )
        except FileExistsError as error:
            raise DomainRuleError(
                "PROJECT_ROOT_EXISTS", "项目目录已存在，未写入或删除该目录", {"code": code}
            ) from error
        except OSError as error:
            if (Path(self.settings.projects_root) / code).exists():
                raise DomainRuleError(
                    "PROJECT_ROOT_EXISTS", "项目目录已存在，未写入或删除该目录", {"code": code}
                ) from error
            raise
        return root

    def _read_back(self, project_id: str, video_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            project_row = connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
            if project_row is None:
                raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在", {"project_id": project_id})
            project = {key: project_row[key] for key in project_row.keys()}
            video_row = connection.execute("SELECT * FROM explainer_videos WHERE id = ?", (video_id,)).fetchone()
            if video_row is None:
                raise DomainRuleError("VIDEO_NOT_FOUND", "解说作品不存在", {"project_id": project_id})
            video = {key: video_row[key] for key in video_row.keys()}
        for key in ("input_payload_json",):
            raw = video.get(key)
            if isinstance(raw, str):
                try:
                    video[key] = json.loads(raw)
                except json.JSONDecodeError:
                    video[key] = {}
            elif raw is None:
                video[key] = {}
        return {"project": project, "video": video}

    # -------------------------------------------------------------- idempotency
    def lookup_create_response(self, idempotency_key: str) -> dict[str, Any] | None:
        """Read a stored composite receipt without creating anything."""

        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT response_json FROM command_idempotencies WHERE scope=? AND idempotency_key=?",
                (EXPLAINER_CREATE_IDEMPOTENCY_SCOPE, str(idempotency_key).strip()),
            ).fetchone()
        if row is None:
            return None
        try:
            return json.loads(str(row["response_json"]))
        except json.JSONDecodeError:
            return None


def reference_urls_are_supported(urls: Sequence[str]) -> dict[str, Any]:
    """Report whether the declared reference links are structurally usable."""

    supported: list[str] = []
    rejected: list[dict[str, str]] = []
    for url in list(urls)[:MAX_REFERENCE_URLS]:
        split = urlsplit(str(url))
        if split.scheme in {"http", "https"} and split.netloc:
            supported.append(str(url))
        else:
            rejected.append({"url": str(url), "reason": "NOT_HTTP_URL"})
    return {"supported": supported, "rejected": rejected}


def build_explainer_creation_service(
    database: DatabaseUnitOfWork,
    settings: Any,
    *,
    project_service: Any | None = None,
    production_service: Any | None = None,
) -> ExplainerCreationService:
    """Composition root for the explainer creation command.

    The concrete collaborators are wired here rather than inside the service, so the
    application layer keeps depending on injected ports instead of constructing other
    services itself.
    """

    from local_drama.application.explainers.production import ExplainerProductionService
    from local_drama.application.projects import ProjectService

    return ExplainerCreationService(
        database,
        settings,
        project_service=project_service or ProjectService(database, settings.projects_root),
        production_service=production_service or ExplainerProductionService(database),
    )

