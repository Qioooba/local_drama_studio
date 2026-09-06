"""Resolve approved character packs into executable, auditable image inputs."""

from typing import Any

from local_drama.application.character_identity_packs import CharacterIdentityPackService
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.image_input_roles import IDENTITY_REFERENCE_ROLES


def shot_identity_references(connection: Any, project_id: str, shot_id: str,
                             workflow_bindings: dict[str, Any]) -> dict[str, Any]:
    snapshot = CharacterIdentityPackService.generation_snapshot_for_intent(
        connection, {"owner_type": "SHOT", "owner_id": shot_id, "project_id": project_id},
    )
    packs = (snapshot or {}).get("packs", [])
    roles = [role for role in IDENTITY_REFERENCE_ROLES if role in workflow_bindings]
    if not roles and "REFERENCE_IMAGE" in workflow_bindings:
        roles = ["REFERENCE_IMAGE"]
    if len(packs) > len(roles):
        raise DomainRuleError(
            "SHOT_IDENTITY_REFERENCE_ROUTE_REQUIRED",
            f"镜头绑定了 {len(packs)} 个人物身份包，当前单画幅工作流仅支持 {len(roles)} 张身份参考图；请在模型配置中选择支持足够参考图的单画幅工作流",
            {"required_reference_count": len(packs), "available_reference_roles": roles},
        )
    references = []
    for index, pack in enumerate(packs):
        front = next((slot for slot in pack["slots"] if slot["slot_kind"] == "FRONT"), None)
        if front is None:
            raise DomainRuleError("IDENTITY_PACK_FRONT_REQUIRED", "身份包缺少已批准正面参考图")
        asset = connection.execute("SELECT name,description FROM story_assets WHERE id=?",
                                   (pack["story_asset_id"],)).fetchone()
        references.append({
            "role": roles[index], "ordinal": 0, "media_version_id": front["media_version_id"],
            "sha256": front["sha256"], "story_asset_id": pack["story_asset_id"],
            "character_name": str(asset["name"]), "description": str(asset["description"] or ""),
            "pack_version_id": pack["pack_version_id"], "pack_content_hash": pack["content_hash"],
        })
    instructions = []
    for index, ref in enumerate(references, 1):
        instructions.append(f"参考图{index}对应人物{ref['character_name']}：{ref['description'][:400]}")
    if instructions:
        instructions.append("严格保持各人物参考图的脸型、性别、发型和服装，不要交换人物身份。仅绘制镜头描述中出场的人物；参考图不是必须全部出场的合影指令。按镜头描述重新构图，输出一张单镜头画面，不要拼接参考图。")
    return {"snapshot_hash": (snapshot or {}).get("snapshot_hash"),
            "references": references, "prompt": "；".join(instructions)}
