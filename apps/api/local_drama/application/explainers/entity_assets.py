"""Bind an explainer entity to the project's shared story asset (design §B3.1/§B3.3).

Why this module exists
----------------------
Step 2 adopts a picture onto a **shared** ``story_assets`` row: ``story_asset_references``
is keyed by that asset, the generation input bridge reads it back, and the identity
pack service resolves a character through it.  An explainer entity that was extracted
without one therefore cannot hold a reference at all — the adopt command has nowhere to
write, so the whole "生成 / 上传 / 采用" flow on step 2 is unusable for that object.

The design states the rule for the detection stage: "生成实体建议并复用已存在资产；匹配已
有 ID".  This module is the single implementation of that rule:

* an entity that already points at an ACTIVE asset keeps it (idempotent);
* otherwise an ACTIVE asset of the same project, kind and name is **reused** — the
  project must never end up with two assets for one object;
* otherwise one is created with a project-unique code.

Nothing here invents a media version, a reference or a state: the new asset starts
empty, exactly like an Asset-Bible-created asset does.
"""

from __future__ import annotations

from typing import Any, Mapping

from local_drama.domain.errors import DomainRuleError

__all__ = ["ensure_entity_story_asset", "story_asset_kind_for_entity"]

from local_drama.application.explainers.contracts_v2 import asset_kind_for_entity_type


def story_asset_kind_for_entity(entity_type: str) -> str:
    """The ``story_assets.kind`` an entity type maps to.

    ``story_assets`` only knows CHARACTER/SCENE/PROP, so this reuses the planner's own
    mapping instead of defining a second one that could drift from the prompt compiler.
    """

    return asset_kind_for_entity_type(entity_type)


def _unique_code(repo: Any, project_id: str, code: str) -> str:
    """A code no other asset of this project uses (codes are unique per project)."""

    candidate = (code or "ASSET")[:80]
    suffix = 1
    while repo.query_one(
        "SELECT 1 FROM story_assets WHERE project_id = ? AND code = ?", (project_id, candidate)
    ):
        candidate = f"{(code or 'ASSET')[:70]}_{suffix}"
        suffix += 1
        if suffix > 99:
            import uuid

            candidate = f"{(code or 'ASSET')[:60]}_{uuid.uuid4().hex[:6]}"
            break
    return candidate


def ensure_entity_story_asset(
    repo: Any,
    *,
    project_id: str,
    entity: Mapping[str, Any],
    actor: str = "system",
) -> dict[str, Any]:
    """Return the entity's shared asset, creating or reusing one when needed.

    The caller gets ``created``/``reused`` so a receipt can say what actually happened
    instead of claiming an asset was created when an existing one was matched.
    """

    entity_id = str(entity.get("id") or entity.get("entity_id") or "")
    if not entity_id:
        raise DomainRuleError("EXPLAINER_ENTITY_NOT_FOUND", "实体不存在，无法建立共享资产。")
    existing_id = str(entity.get("story_asset_id") or "")
    if existing_id:
        current = repo.find("story_assets", existing_id)
        if current is not None and str(current.get("status") or "ACTIVE").upper() == "ACTIVE":
            return {
                "story_asset_id": existing_id,
                "created": False,
                "reused": True,
                "linked": False,
                "kind": str(current.get("kind") or ""),
            }

    kind = story_asset_kind_for_entity(str(entity.get("entity_type") or ""))
    name = str(entity.get("name") or entity.get("code") or "").strip()
    matched = None
    if name:
        matched = repo.query_one(
            """SELECT id, kind FROM story_assets
                WHERE project_id = ? AND kind = ? AND status = 'ACTIVE' AND lower(name) = lower(?)""",
            (project_id, kind, name),
        )
    if matched is not None:
        asset_id = str(matched["id"])
        created = False
    else:
        payload: dict[str, Any] = {
            "project_id": project_id,
            "kind": kind,
            "code": _unique_code(repo, project_id, str(entity.get("code") or name or "ASSET")),
            "name": name or str(entity.get("code") or "对象"),
            "description": str(entity.get("description") or "").strip(),
            "canonical_media_version_id": None,
            "extra_json": {"source": "EXPLAINER_ENTITY_DETECTION", "entity_type": str(entity.get("entity_type") or "")},
            "status": "ACTIVE",
        }
        created_row = repo.insert("story_assets", payload, actor=actor)
        asset_id = str(created_row["id"])
        created = True

    repo.update(
        "explainer_entities",
        entity_id,
        {"story_asset_id": asset_id},
        actor=actor,
    )
    return {
        "story_asset_id": asset_id,
        "created": created,
        "reused": not created,
        "linked": True,
        "kind": kind,
    }
