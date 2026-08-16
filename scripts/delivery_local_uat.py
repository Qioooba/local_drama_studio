"""Run an isolated Windows/local-only timeline and delivery UAT.

The UAT starts from a real, locally generated H3 artifact, but all platform
state is created under an explicit sandbox root.  It exercises the public
FastAPI timeline/review/configuration/delivery endpoints and records the
immutable delivery manifest, verify/history/download/withdraw lifecycle.
The evidence is intentionally ``PARTIAL``: an isolated run is not production
release sign-off and cannot prove a multi-episode rollout or long-running
Windows stability.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.reviews import ReviewService
from local_drama.config import Settings
from local_drama.infrastructure.database.sqlite import Database
from local_drama.main import create_app

from scripts.migrate import migrate


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _settings(root: Path) -> Settings:
    return Settings(
        data_root=root / "data",
        projects_root=root / "projects",
        work_root=root / "work",
        cache_root=root / "cache",
        logs_root=root / "logs",
        backups_root=root / "backups",
    )


def _expect(response: Any, code: int, label: str) -> dict[str, Any]:
    if response.status_code != code:
        raise RuntimeError(f"{label}: expected HTTP {code}, got {response.status_code}: {response.text[:500]}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise TypeError(f"{label}: response must be an object")
    return payload


def _review_checks(template: dict[str, Any]) -> list[dict[str, str]]:
    return [{"item_id": str(item["id"]), "result": "PASS"} for item in template["items"]]


def run(artifact: Path, sandbox_root: Path) -> dict[str, Any]:
    source = artifact.resolve(strict=True)
    if not source.is_file() or source.is_symlink():
        raise ValueError("artifact must be a regular local file")
    sandbox = sandbox_root.resolve()
    # Keep this script from accidentally treating the production project tree
    # (or one of its ancestors) as an isolated root.  The normal invocation
    # uses work/... under the repository.
    if sandbox == ROOT.resolve() or ROOT.resolve().is_relative_to(sandbox):
        raise ValueError("sandbox root must be a dedicated directory")
    if sandbox.exists() and any(sandbox.iterdir()):
        raise ValueError(f"sandbox root must be new and empty: {sandbox}")
    sandbox.mkdir(parents=True, exist_ok=False)

    settings = _settings(sandbox)
    settings.ensure_roots()
    migrate(settings.database_path)
    database = Database(settings.database_path)
    project_service = ProjectService(database, settings.projects_root)
    project = project_service.create_project(
        code="h3_delivery_local_uat",
        title="H3 Windows local delivery isolated UAT",
        episode_count=1,
        aspect_ratio="9:16",
        fps_num=24,
        fps_den=1,
        target_duration_ms=5_167,
        allow_unconfigured_capabilities=True,
    )
    project_id = str(project["id"])
    season = project_service.list_seasons(project_id)[0]
    episode = project_service.list_episodes(str(season["id"]))[0]
    shot = project_service.create_shot(str(episode["id"]), "H3-001", 5_167)

    media = MediaService(database, settings).import_file(
        project_id,
        source,
        purpose="SHOT_VIDEO",
        owner_id=str(shot["id"]),
        media_kind="VIDEO",
        stage="FORMAL",
    )
    media_version_id = str(media["media_version_id"])
    reviews = ReviewService(database, settings)
    reviews.ensure_templates()
    machine = reviews.machine_check(media_version_id)
    selected = reviews.select_version(media_version_id, "FORMAL_SELECTION")
    formal_template = next(item for item in reviews.templates() if item["code"] == "formal_video")
    subject_revision = int(reviews.review_context(media_version_id)["subject_revision"])
    media_review = reviews.submit_review(
        media_version_id,
        str(formal_template["id"]),
        "APPROVED",
        expected_subject_revision=subject_revision,
        checks=_review_checks(formal_template),
        comment="真实 H3 本地媒体整集交付 UAT 批准",
    )
    formal_plan = reviews.formal_selection_preflight(project_id, [media_version_id])
    formal_commit = reviews.commit_formal_selection(project_id, [media_version_id], str(formal_plan["plan_hash"]))

    with TestClient(create_app(settings)) as client:
        timeline_payload = {
            "items": [
                {
                    "track_type": "VIDEO",
                    "media_version_id": media_version_id,
                    "start_us": 0,
                    "end_us": 5_167_000,
                    "parameters": {"source": "formal_selection", "source_sha256": media["sha256"]},
                }
            ],
            "input_snapshot": {
                "source": "h3_formal_pipeline_uat",
                "formal_selection_id": formal_commit["items"][0]["id"],
                "media_version_id": media_version_id,
            },
        }
        timeline = _expect(
            client.post(f"/api/v1/episodes/{episode['id']}/timeline-revisions", json=timeline_payload),
            201,
            "create timeline",
        )["timeline"]
        render = _expect(client.post(f"/api/v1/timeline-revisions/{timeline['id']}:render"), 201, "render episode")["render"]

        target_spec = {
            "path_rel": "06_delivery/本地 交付 UAT",
            "width": 480,
            "height": 832,
            "fps": 24,
            "bitrate": "1M",
            "audio_codec": "AAC",
            "subtitles": "SIDECAR",
        }
        target = _expect(
            client.post(
                f"/api/v1/projects/{project_id}/delivery-targets",
                json={"code": "windows-local-uat", "title": "Windows 本地交付 UAT", "transport": "LOCAL_FILESYSTEM", "spec": target_spec},
            ),
            201,
            "create local delivery target",
        )["target"]
        selected_target = _expect(
            client.post(f"/api/v1/delivery-target-versions/{target['version_id']}:select", params={"project_id": project_id}),
            200,
            "select local delivery target",
        )["target"]

        brand = _expect(
            client.post(
                f"/api/v1/projects/{project_id}/brand-kits",
                json={"code": "series", "title": "H3 本地系列 v1", "tokens": {"colors": {"primary": "#223344"}}},
            ),
            201,
            "create brand kit",
        )["brand_kit"]
        watermark = _expect(
            client.post(
                f"/api/v1/projects/{project_id}/watermark-profiles",
                json={
                    "code": "corner",
                    "title": "Local Study watermark",
                    "config": {"text": "LOCAL STUDY", "position": "BOTTOM_RIGHT", "opacity": 0.8, "font_size": 18, "margin": 8, "color": "white"},
                },
            ),
            201,
            "create watermark profile",
        )["watermark_profile"]
        failing_policy = _expect(
            client.post(
                f"/api/v1/projects/{project_id}/compliance-policies",
                json={"code": "duration", "title": "故意失败的本地合规策略", "rules": {"require_watermark": True, "max_duration_ms": 100}},
            ),
            201,
            "create failing compliance policy",
        )["compliance_policy"]
        control_ids = {"brand_kit_id": brand["id"], "watermark_profile_id": watermark["id"]}
        failed_preflight = client.post(
            "/api/v1/delivery-packages",
            json={"episode_render_version_id": render["id"], "target_version_id": target["version_id"], **control_ids, "compliance_policy_id": failing_policy["id"]},
        )
        failed_preflight_payload = _expect(failed_preflight, 422, "failing compliance preflight")
        passing_policy = _expect(
            client.post(
                f"/api/v1/projects/{project_id}/compliance-policies",
                json={"code": "duration", "title": "本地合规策略 v2", "rules": {"require_watermark": True, "max_duration_ms": 10_000, "require_human_review": True, "require_platform_review": True}},
            ),
            201,
            "create passing compliance policy",
        )["compliance_policy"]
        control_ids["compliance_policy_id"] = passing_policy["id"]

        # Prove that the immutable render cannot be handed off before its
        # latest human approval.  No output directory should be touched here.
        blocked = client.post(
            "/api/v1/delivery-packages",
            json={"episode_render_version_id": render["id"], "target_version_id": target["version_id"], **control_ids},
        )
        blocked_payload = _expect(blocked, 422, "pre-approval delivery gate")
        blocked_code = str(blocked_payload.get("error", {}).get("code", ""))

        render_template = next(item for item in client.get("/api/v1/review-templates").json()["items"] if item["code"] == "episode_render")
        render_review = _expect(
            client.post(
                f"/api/v1/subjects/EPISODE_RENDER_VERSION/{render['id']}/reviews",
                json={
                    "template_version_id": render_template["id"],
                    "decision": "APPROVED",
                    "expected_subject_revision": int(render["revision"]),
                    "checks": _review_checks(render_template),
                    "comment": "整集最新渲染版本已通过本地人工检查",
                },
            ),
            201,
            "approve latest episode render",
        )["review"]
        delivery = _expect(
            client.post(
                "/api/v1/delivery-packages",
                json={"episode_render_version_id": render["id"], "target_version_id": selected_target["version_id"], **control_ids},
            ),
            201,
            "build delivery package",
        )["delivery"]
        package_id = str(delivery["id"])
        project_root = settings.projects_root / str(project["root_rel"])
        package_root = project_root / str(delivery["rel_path"])
        manifest_path = package_root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        verify = _expect(client.get(f"/api/v1/delivery-packages/{package_id}:verify"), 200, "verify delivery")["delivery"]
        details_before_download = _expect(client.get(f"/api/v1/delivery-packages/{package_id}"), 200, "get delivery details")["delivery"]
        files = _expect(client.get(f"/api/v1/delivery-packages/{package_id}/files"), 200, "list delivery files")["items"]
        human_review = _expect(
            client.post(f"/api/v1/delivery-packages/{package_id}:review", json={"reviewer_type": "HUMAN", "decision": "APPROVED", "note": "隔离 UAT 人工确认画面与本地授权范围"}),
            200,
            "human delivery review",
        )["delivery"]
        platform_review = _expect(
            client.post(f"/api/v1/delivery-packages/{package_id}:review", json={"reviewer_type": "PLATFORM", "decision": "APPROVED", "note": "隔离 UAT 平台规则确认"}),
            200,
            "platform delivery review",
        )["delivery"]
        download = client.get(f"/api/v1/delivery-packages/{package_id}/download")
        if download.status_code != 200 or not download.content:
            raise RuntimeError(f"download delivery: expected non-empty HTTP 200, got {download.status_code}")
        details_after_download = _expect(client.get(f"/api/v1/delivery-packages/{package_id}"), 200, "get delivery history")["delivery"]
        withdrawn = _expect(
            client.post(f"/api/v1/delivery-packages/{package_id}:withdraw", json={"reason": "Windows 本地 UAT 撤回验证"}),
            200,
            "withdraw delivery",
        )["delivery"]
        verify_withdrawn = _expect(client.post(f"/api/v1/delivery-packages/{package_id}:verify"), 200, "verify withdrawn delivery")["delivery"]
        details_after_withdraw = _expect(client.get(f"/api/v1/delivery-packages/{package_id}"), 200, "get withdrawn delivery history")["delivery"]
        history = _expect(client.get(f"/api/v1/episodes/{episode['id']}/delivery-packages"), 200, "list delivery history")["items"]

    event_actions = [str(item["action"]) for item in details_after_withdraw["events"]]
    output_path = package_root / f"{episode['code']}.mp4"
    if not output_path.is_file() or not manifest_path.is_file():
        raise RuntimeError("delivery output or manifest missing after withdraw")
    if blocked_code not in {"EPISODE_RENDER_APPROVAL_REQUIRED", "EPISODE_RENDER_APPROVAL_STALE"}:
        raise RuntimeError(f"unexpected pre-approval gate code: {blocked_code}")
    if verify["status"] != "VERIFIED" or withdrawn["status"] != "WITHDRAWN" or verify_withdrawn["status"] != "WITHDRAWN":
        raise RuntimeError("delivery lifecycle did not reach VERIFIED -> WITHDRAWN")
    if not {"BUILT", "VERIFY", "DOWNLOAD", "WITHDRAWN"}.issubset(set(event_actions)):
        raise RuntimeError(f"delivery history is incomplete: {event_actions}")
    withdrawn_event = next(item for item in details_after_withdraw["events"] if item["action"] == "WITHDRAWN")
    if withdrawn_event.get("manifest_sha256") != delivery.get("manifest_sha256"):
        raise RuntimeError("withdrawal event lost the immutable delivery manifest hash")
    if len(download.content) != output_path.stat().st_size:
        raise RuntimeError("downloaded bytes differ from local delivery file")

    with database.connect() as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    return {
        "schema_version": "g10.delivery_local_windows_uat.v1",
        "status": "PARTIAL",
        "observed_at": datetime.now(UTC).isoformat(),
        "scope": "真实 H3 本地媒体进入隔离 Windows/F 盘路径的整集 Timeline -> Delivery 生命周期",
        "source": {"path": str(source), "sha256": _sha256(source), "bytes": source.stat().st_size},
        "isolated": {
            "sandbox_root": str(sandbox),
            "project_id": project_id,
            "project_root_rel": str(project["root_rel"]),
            "database_integrity": integrity,
            "production_database_contacted": False,
            "production_project_tree_mutated": False,
            "runtime_contacted": False,
            "network_contacted": False,
        },
        "checks": {
            "formal_media_machine_qc": machine,
            "formal_media_selection": selected,
            "formal_media_review": media_review,
            "formal_selection_preflight": formal_plan,
            "formal_selection_commit": formal_commit,
            "timeline_revision": timeline,
            "episode_render": render,
            "pre_approval_delivery_gate": {"status_code": blocked.status_code, "error_code": blocked_code},
            "failed_compliance_preflight": {"status_code": failed_preflight.status_code, "error_code": failed_preflight_payload.get("error", {}).get("code")},
            "latest_render_review": render_review,
            "delivery_target": selected_target,
            "controls": {"brand_kit": brand, "watermark_profile": watermark, "failing_policy": failing_policy, "passing_policy": passing_policy},
            "delivery": delivery,
            "manifest": {
                "path_rel": str(manifest_path.relative_to(sandbox)).replace("\\", "/"),
                "schema_version": manifest.get("schema_version"),
                "manifest_sha256": delivery["manifest_sha256"],
                "file_count": len(manifest.get("files", [])),
                "target_transport": manifest.get("target", {}).get("transport"),
                "model_license": manifest.get("licenses", {}).get("model"),
            },
            "verify": verify,
            "details_before_download": details_before_download,
            "files": files,
            "download": {"status_code": download.status_code, "bytes": len(download.content), "content_disposition": download.headers.get("content-disposition")},
            "human_delivery_review": human_review,
            "platform_delivery_review": platform_review,
            "history_after_download": details_after_download,
            "withdraw": withdrawn,
            "verify_after_withdraw": verify_withdrawn,
            "history_after_withdraw": details_after_withdraw,
            "episode_history": {"count": len(history), "package_ids": [str(item["id"]) for item in history]},
            "preserved_output": {"path_rel": str(output_path.relative_to(sandbox)).replace("\\", "/"), "sha256": _sha256(output_path), "bytes": output_path.stat().st_size},
        },
        "limitations": [
            "本次运行使用独立 SQLite/项目根，未写入生产数据库或生产项目树。",
            "整集只有一个真实 H3 镜头，未证明多镜头/多集长期稳定性。",
            "交付候选仍保留 HUMAN/PLATFORM 审核责任边界；本次仅验证整集最新批准门禁与本地生命周期，不伪造最终发布签字。",
            "LOCAL_ONLY 本机运行未证明用户自有模型的许可范围；模型仍只作为本机引用，不打包、不上传。",
        ],
        "related_requirements": ["FR-DEL-001", "FR-DEL-002", "FR-DEL-003", "FR-DEL-004", "FR-PST-002", "FR-PST-003"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--sandbox-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = run(args.artifact, args.sandbox_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": payload["status"], "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
