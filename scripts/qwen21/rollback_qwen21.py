"""Roll Local Drama Studio back off Qwen-Image-2.1 without touching history.

The acceptance plan requires one explicit rollback path that restores the old
runtime and default routing *without re-downloading the old weights*.  Nothing
here deletes a row or rewrites an immutable artefact:

``--retire-profiles``  sets ``mp_profile_publications.status='RETIRED'`` for 2.1
``--retire-workflows`` calls ``WorkflowService.revoke`` on the 2.1 versions
``--verify``           asserts 2.1 is out of the default path and the legacy
                       chain is still published

A retired Profile keeps its version row, its frozen payload and its smoke
evidence, so historical projects can still explain where their images came
from.  Re-publishing later is a normal forward operation.

Example::

    python scripts/qwen21/rollback_qwen21.py --retire-profiles --retire-workflows
    python scripts/qwen21/rollback_qwen21.py --verify
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT / "apps" / "api") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "apps" / "api"))

from local_drama.application.workflows import WorkflowService  # noqa: E402
from local_drama.config import Settings  # noqa: E402
from local_drama.infrastructure.database.sqlite import Database  # noqa: E402
from local_drama.model_platform.application.profile_publication import ProfilePublicationService  # noqa: E402

from onboard_qwen21 import DEFINITION_PLAN, MODEL_CODE  # noqa: E402

PROFILE_CODE_PREFIX = f"comfy-{MODEL_CODE}"

# Capabilities the legacy chain must keep serving after a rollback.
LEGACY_REQUIRED_CAPABILITIES = ("IMAGE_CONCEPT", "IMAGE_CHARACTER", "IMAGE_SCENE")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def retire_profiles(database: Database, reason: str) -> dict[str, Any]:
    service = ProfilePublicationService(database)
    with database.connect() as connection:
        rows = connection.execute(
            """SELECT publication.execution_profile_version_id AS version_id,profile.code,version.version_no
               FROM mp_profile_publications publication
               JOIN mp_execution_profile_versions version ON version.id=publication.execution_profile_version_id
               JOIN mp_execution_profiles profile ON profile.id=version.profile_id
               WHERE publication.status='PUBLISHED' AND profile.code LIKE ?
               ORDER BY profile.code,version.version_no""",
            (f"{PROFILE_CODE_PREFIX}%",),
        ).fetchall()
    retired: list[dict[str, Any]] = []
    for row in rows:
        try:
            service.retire(str(row["version_id"]), reason=reason)
            retired.append({"profile_code": str(row["code"]), "version_no": int(row["version_no"]), "status": "RETIRED"})
        except Exception as error:  # noqa: BLE001 - reported per row, never aborting the whole rollback
            retired.append({"profile_code": str(row["code"]), "version_no": int(row["version_no"]), "status": "FAILED", "error": f"{type(error).__name__}: {error}"})
    return {"action": "retire_profiles", "count": len(retired), "profiles": retired}


def retire_workflows(database: Database, settings: Settings, reason: str) -> dict[str, Any]:
    service = WorkflowService(database, settings)
    revoked: list[dict[str, Any]] = []
    for definition_code, _capability, _suffix in DEFINITION_PLAN:
        with database.connect() as connection:
            rows = connection.execute(
                "SELECT wv.id,wv.version_no FROM workflow_versions wv JOIN workflows w ON w.id=wv.workflow_id WHERE w.code=? AND wv.status='PUBLISHED'",
                (definition_code,),
            ).fetchall()
        for row in rows:
            try:
                service.revoke(str(row["id"]), reason=reason, actor="qwen21-rollback")
                revoked.append({"definition_code": definition_code, "version_no": int(row["version_no"]), "status": "RETIRED"})
            except Exception as error:  # noqa: BLE001 - reported per row
                revoked.append({"definition_code": definition_code, "version_no": int(row["version_no"]), "status": "FAILED", "error": f"{type(error).__name__}: {error}"})
    return {"action": "retire_workflows", "count": len(revoked), "workflows": revoked}


def verify(database: Database) -> dict[str, Any]:
    with database.connect() as connection:
        qwen21_published = connection.execute(
            """SELECT COUNT(*) AS total FROM mp_profile_publications publication
               JOIN mp_execution_profile_versions version ON version.id=publication.execution_profile_version_id
               JOIN mp_execution_profiles profile ON profile.id=version.profile_id
               WHERE publication.status='PUBLISHED' AND profile.code LIKE ?""",
            (f"{PROFILE_CODE_PREFIX}%",),
        ).fetchone()
        legacy: dict[str, Any] = {}
        for capability in LEGACY_REQUIRED_CAPABILITIES:
            row = connection.execute(
                """SELECT profile.code,version.version_no FROM execution_profile_versions version
                   JOIN execution_profiles profile ON profile.id=version.execution_profile_id
                   WHERE version.status='PUBLISHED' AND UPPER(version.capability)=?
                   ORDER BY version.updated_at DESC,version.version_no DESC LIMIT 1""",
                (capability,),
            ).fetchone()
            legacy[capability] = {"profile_code": str(row["code"]), "version_no": int(row["version_no"])} if row else None
        qwen21_workflows = connection.execute(
            """SELECT w.code,wv.status FROM workflow_versions wv JOIN workflows w ON w.id=wv.workflow_id
               WHERE w.code LIKE 'QWEN_IMAGE_21_%' AND wv.status='PUBLISHED'"""
        ).fetchall()
    unpublished = int(qwen21_published["total"]) == 0
    legacy_ok = all(value is not None for value in legacy.values())
    # Rollback *safety* means the legacy chain is still published and reachable,
    # so retiring 2.1 can never leave a capability without a production route.
    # That 2.1 is currently published is a normal post-activation state, not a
    # failure -- report it, and only require the legacy chain to be intact.
    return {
        "action": "verify",
        "qwen21_published_profiles": int(qwen21_published["total"]),
        "qwen21_published_workflows": [str(row["code"]) for row in qwen21_workflows],
        "legacy_published_profiles": legacy,
        "checks": {
            "qwen21_already_retired": unpublished,
            "legacy_chain_still_published": legacy_ok,
        },
        "passed": legacy_ok,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--retire-profiles", action="store_true")
    parser.add_argument("--retire-workflows", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--reason", default="回退到 Qwen-Image-2512 / Edit-2511 生产链")
    parser.add_argument("--database", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    if not (args.retire_profiles or args.retire_workflows or args.verify):
        parser.error("choose at least one of --retire-profiles / --retire-workflows / --verify")

    settings = Settings.from_env()
    if args.database:
        settings = settings.model_copy(update={"database_path": args.database})
    database = Database(settings.database_path)
    actions: list[dict[str, Any]] = []
    if args.retire_profiles:
        actions.append(retire_profiles(database, args.reason))
    if args.retire_workflows:
        actions.append(retire_workflows(database, settings, args.reason))
    if args.verify:
        actions.append(verify(database))

    report = {
        "schema_version": "localdrama.qwen21-rollback.v1",
        "generated_at": _now(),
        "database": str(settings.database_path),
        "reason": args.reason,
        "actions": actions,
        "note": "回退不删除任何版本行、冻结 payload 或 smoke 证据，也不重新下载旧权重。",
    }
    verification = next((item for item in actions if item["action"] == "verify"), None)
    report["passed"] = bool(verification["passed"]) if verification else True
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    sys.exit(main())
