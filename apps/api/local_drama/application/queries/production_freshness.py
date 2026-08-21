"""Unified query-time production freshness evaluation."""

from __future__ import annotations

from typing import Any

from local_drama.application.ports.production_freshness import ProductionFreshnessPort
from local_drama.domain.errors import DomainRuleError


class ProductionFreshnessService:
    """Aggregate existing stale flags and immutable source/current facts.

    This service is intentionally read-only.  Historical variants, anchors,
    approvals and timeline revisions remain facts even when current production
    validity changes.
    """

    def __init__(self, repository: ProductionFreshnessPort) -> None:
        self.repository = repository

    def evaluate(self, *, scope_type: str, scope_id: str, limit: int = 100) -> dict[str, Any]:
        kind = scope_type.upper()
        if kind not in {"PROJECT", "EPISODE", "SHOT"}:
            raise DomainRuleError("FRESHNESS_SCOPE_INVALID", "freshness scope 必须是 PROJECT、EPISODE 或 SHOT")
        if limit < 1 or limit > 500:
            raise DomainRuleError("FRESHNESS_LIMIT_INVALID", "limit 必须在 1 到 500 之间")
        facts = self.repository.load_facts(scope_type=kind, scope_id=scope_id, limit=limit + 1)
        if not facts.get("scope"):
            raise DomainRuleError(f"{kind}_NOT_FOUND", f"{kind.lower()} 不存在", {f"{kind.lower()}_id": scope_id})

        items: list[dict[str, Any]] = []
        variant_reasons: dict[str, list[dict[str, Any]]] = {}
        grouped_variants: dict[str, list[dict[str, Any]]] = {}
        for row in facts.get("variants", []):
            grouped_variants.setdefault(str(row["variant_id"]), []).append(row)
        for variant_id, rows in grouped_variants.items():
            reasons = self._unique([reason for row in rows for reason in self._variant_reasons(row)])
            row = next((candidate for candidate in rows if self._variant_reasons(candidate)), rows[0])
            variant_reasons[variant_id] = reasons
            items.append(self._item(
                fact_type="VARIANT", fact_id=variant_id, row=row, reasons=reasons,
                source=self._version("ASSET_REFERENCE", row.get("source_reference_id"), row.get("source_revision")),
                current=self._version("ASSET_REFERENCE", row.get("current_reference_id"), row.get("current_revision")),
                remediation=[
                    self._link("regenerate", f"/director/{row['shot_id']}?action=regenerate", "仅重生成当前镜"),
                    self._link("rebuild-continuity", f"/director/{row['shot_id']}?action=rebuild-continuity", "重建当前镜及连续性"),
                ],
            ))

        for row in facts.get("frame_bridges", []):
            reasons: list[dict[str, Any]] = []
            if int(row.get("is_stale") or 0):
                reasons.append(self._reason("FRAME_BRIDGE_CHANGED", row.get("stale_reason") or "Frame Bridge 上游已变化"))
            upstream_id = row.get("variant_id")
            for reason in variant_reasons.get(str(upstream_id), []) if upstream_id else []:
                reasons.append({**reason, "propagated_from": "VARIANT"})
            items.append(self._item(
                fact_type="FRAME_BRIDGE", fact_id=str(row["frame_anchor_id"]), row=row,
                reasons=self._unique(reasons),
                source=self._version("MEDIA_VERSION", row.get("source_media_version_id"), row.get("source_media_version_no")),
                current=self._version("MEDIA_VERSION", row.get("current_media_version_id"), row.get("current_media_version_no")),
                remediation=[self._link("rebuild-continuity", f"/director/{row['shot_id']}?action=rebuild-continuity", "重新继承 Frame Bridge")],
            ))

        for row in facts.get("timeline", []):
            reasons = []
            if str(row.get("timeline_status") or "").upper() == "STALE":
                reasons.append(self._reason("SHOT_REVISION_CHANGED", "时间线 revision 已被标记 stale"))
            upstream_id = row.get("variant_id")
            for reason in variant_reasons.get(str(upstream_id), []) if upstream_id else []:
                reasons.append({**reason, "propagated_from": "VARIANT"})
            current_media = row.get("current_media_version_id")
            if current_media and str(current_media) != str(row.get("media_version_id")):
                reasons.append(self._reason(
                    "SELECTION_CHANGED", "当前选择已变化，时间线仍保留旧媒体版本",
                    source_revision=row.get("media_version_no"), current_revision=row.get("current_media_version_no"),
                ))
            source_recipe = row.get("source_post_process_recipe_id")
            current_recipe = row.get("current_post_process_recipe_id")
            if source_recipe and str(source_recipe) != str(current_recipe or ""):
                reasons.append(self._reason(
                    "POST_PROCESS_CHANGED", "时间线媒体使用的增强 Recipe 已不是当前 ACTIVE 版本",
                    source_revision=row.get("source_post_process_version_no"), current_revision=row.get("current_post_process_version_no"),
                ))
            items.append(self._item(
                fact_type="TIMELINE", fact_id=str(row["timeline_item_id"]), row=row,
                reasons=self._unique(reasons),
                source=self._version("MEDIA_VERSION", row.get("media_version_id"), row.get("media_version_no")),
                current=self._version("MEDIA_VERSION", current_media, row.get("current_media_version_no")),
                remediation=[self._link("create-timeline-revision", f"/episodes/{row['episode_id']}/timeline?action=update-shot&shot_id={row.get('shot_id') or ''}", "创建新时间线 revision")],
            ))

        order = {"VARIANT": 0, "FRAME_BRIDGE": 1, "TIMELINE": 2}
        items.sort(key=lambda item: (order[item["fact_type"]], item.get("shot_id") or "", item["id"]))
        truncated = len(items) > limit or bool(facts.get("truncated"))
        items = items[:limit]
        stale = sum(item["status"] == "STALE" for item in items)
        return {
            "scope": facts["scope"],
            "summary": {"returned": len(items), "stale": stale, "current": len(items) - stale, "truncated": truncated},
            "items": items,
            "audit": {"read_only": True, "writes_performed": 0, "query_count": int(facts.get("query_count", 0)), "query_limit": limit},
            "local_only": True,
            "network_contacted": False,
        }

    @staticmethod
    def _variant_reasons(row: dict[str, Any]) -> list[dict[str, Any]]:
        reasons: list[dict[str, Any]] = []
        if int(row.get("is_stale") or 0):
            raw = str(row.get("stale_reason") or "")
            code = "SHOT_REVISION_CHANGED" if "shot" in raw or "beat_replan" in raw else "FRAME_BRIDGE_CHANGED" if "frame" in raw or "continuity" in raw else "ASSET_REFERENCE_CHANGED"
            reasons.append(ProductionFreshnessService._reason(code, raw or "Variant 已被现有 stale 传播标记"))
        source_ref = row.get("source_reference_id")
        # Asset Bible deliberately supports multiple simultaneous view refs.
        # An exact referenced MediaVersion remains current while its reference
        # is ACTIVE; a different active sibling alone must not make it stale.
        if source_ref and str(row.get("source_reference_status") or "").upper() != "ACTIVE":
            reasons.append(ProductionFreshnessService._reason(
                "ASSET_REFERENCE_CHANGED", "Variant 使用的资产参考已不是当前参考",
                source_revision=row.get("source_revision"), current_revision=row.get("current_revision"),
            ))
        source_state = row.get("source_asset_state_id")
        current_state = row.get("current_asset_state_id")
        if source_state and current_state and str(source_state) != str(current_state):
            reasons.append(ProductionFreshnessService._reason(
                "ASSET_STATE_CHANGED", "镜头当前资产状态与 Variant 使用的参考状态不同",
                source_revision=row.get("source_state_revision"), current_revision=row.get("current_state_revision"),
            ))
        source_prompt = row.get("source_prompt_revision_id")
        current_prompt = row.get("current_prompt_revision_id")
        if source_prompt and current_prompt and str(source_prompt) != str(current_prompt):
            reasons.append(ProductionFreshnessService._reason(
                "PROMPT_CHANGED", "Variant 固化的 Prompt 已有更新 revision",
                source_revision=row.get("source_prompt_revision_no"), current_revision=row.get("current_prompt_revision_no"),
            ))
        source_profile = row.get("source_profile_version_id")
        current_profile = row.get("current_profile_version_id")
        if source_profile and str(source_profile) != str(current_profile or ""):
            reasons.append(ProductionFreshnessService._reason(
                "PROFILE_CHANGED", "当前能力解析的 Profile 已不同于 Variant 固化版本",
                source_revision=row.get("source_profile_version_no"), current_revision=row.get("current_profile_version_no"),
            ))
        return ProductionFreshnessService._unique(reasons)

    @staticmethod
    def _reason(code: str, message: str, *, source_revision: Any = None, current_revision: Any = None) -> dict[str, Any]:
        return {"code": code, "message": message, "source_revision": source_revision, "current_revision": current_revision}

    @staticmethod
    def _version(entity_type: str, entity_id: Any, revision: Any) -> dict[str, Any] | None:
        if entity_id is None:
            return None
        return {"entity_type": entity_type, "entity_id": str(entity_id), "revision": revision}

    @staticmethod
    def _link(rel: str, href: str, label: str) -> dict[str, str]:
        return {"rel": rel, "href": href, "method": "GET", "label": label}

    @staticmethod
    def _unique(reasons: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[tuple[Any, ...]] = set()
        result = []
        for reason in reasons:
            key = (reason.get("code"), reason.get("message"), reason.get("source_revision"), reason.get("current_revision"))
            if key not in seen:
                seen.add(key)
                result.append(reason)
        return result

    @staticmethod
    def _item(*, fact_type: str, fact_id: str, row: dict[str, Any], reasons: list[dict[str, Any]], source: dict[str, Any] | None, current: dict[str, Any] | None, remediation: list[dict[str, str]]) -> dict[str, Any]:
        return {
            "id": fact_id, "fact_type": fact_type, "status": "STALE" if reasons else "CURRENT",
            "project_id": str(row["project_id"]), "episode_id": row.get("episode_id"), "shot_id": row.get("shot_id"),
            "source": source, "current": current, "reasons": reasons, "remediation_links": remediation,
        }
