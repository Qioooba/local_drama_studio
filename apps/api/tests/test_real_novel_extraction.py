from fastapi.testclient import TestClient

from local_drama.application.projects import ProjectService
from local_drama.application.worker import LocalMediaWorker
from local_drama.main import create_app


def test_real_novel_chapters_and_entity_extraction(workspace, database, mock_story_pipeline_ai) -> None:
    del mock_story_pipeline_ai
    # 1. Create a project
    project = ProjectService(database, workspace.projects_root).create_project(
        code="real_novel_test",
        title="遮天仙途·真机小说实测",
        episode_count=1,
        aspect_ratio="9:16",
        fps_num=24,
        fps_den=1,
        target_duration_ms=120_000,
        allow_unconfigured_capabilities=True,
    )
    project_id = str(project["id"])

    real_novel_text = """# 遮天仙途 第一卷 少年踏歌行

第一章 重生坠仙谷
清晨，雾气弥漫。林枫猛然睁开双眼，发现自己身处荒凉险峻的坠仙谷中。
他缓缓站起身，低头看着掌心，那里正静静躺着一枚闪烁微光的九阳神丹。
林枫握紧神丹，自语道：“上一世你们夺我道骨，这一世我林枫定要踏碎九天！”
忽然，远处的古树后传来一阵轻微的脚步声。一位身着淡蓝长裙的清冷女子缓缓现身，正是顾清雪。
顾清雪看着林枫，清声道：“你竟然还活着，快拔出斩龙神剑防身，追兵就在身后。”

第二章 踏入青云宗主殿
日暮时分，天边晚霞如血。林枫随同顾清雪一路跋涉，终于来到巍峨庄严的青云宗主殿。
大殿上方，白发老者楚天极负手而立，威严的目光落在林枫身上。
楚天极沉声问道：“林枫，你手中的斩龙神剑，可愿献给宗门？”
林枫冷笑一声：“剑在人在，何须献与他人！”
"""

    with TestClient(create_app(workspace)) as client:
        # 2. Upload real novel document
        upload_res = client.post(
            f"/api/v1/projects/{project_id}/imports:upload",
            content=real_novel_text.encode("utf-8"),
            headers={
                "Content-Type": "text/plain; charset=utf-8",
                "X-File-Name": "zhetian_real_novel.txt",
            },
        )
        assert upload_res.status_code == 201, upload_res.text
        source_doc_version_id = upload_res.json()["import"]["source_document_version_id"]

        # 3. Trigger One-Click Story & Bible Generation
        start_res = client.post(
            f"/api/v1/projects/{project_id}/pipeline:start",
            json={
                "source_document_version_id": source_doc_version_id,
                "visual_style": "国风修仙 电影级写实 (Cinematic Realistic)",
                "target_episode_duration_seconds": 120,
                "auto_run_rendering": False,
            },
        )
        assert start_res.status_code == 200, start_res.text
        run_id = start_res.json()["run"]["run_id"]

        # 4. Persistent worker produces an isolated, reviewable draft.
        worker_result = LocalMediaWorker(database, workspace).run_once("real-novel-pipeline", ["CPU"])
        assert worker_result is not None
        assert worker_result.get("error") is None
        status_res = client.get(f"/api/v1/projects/{project_id}/pipeline/{run_id}")
        assert status_res.status_code == 200
        run_data = status_res.json()["run"]

        assert run_data["state"] == "SUCCEEDED", f"Pipeline error: {run_data.get('error_message')}"
        assert run_data["apply_state"] == "NOT_APPLIED"

        chars = [c["name"] for c in run_data["assets"]["characters"]]
        scenes = [s["name"] for s in run_data["assets"]["scenes"]]
        props = [p["name"] for p in run_data["assets"]["props"]]
        episodes = run_data["episodes"]

        print("\nExtracted Characters:", chars)
        print("Extracted Scenes:", scenes)
        print("Extracted Props:", props)
        print("Planned Episodes:", [e["title"] for e in episodes])

        # Assert real characters are extracted
        assert any("林枫" in c or "顾清雪" in c or "楚天极" in c for c in chars), f"Characters missing real names: {chars}"
        # Assert real scenes are extracted
        assert any("坠仙谷" in s or "青云宗主殿" in s for s in scenes), f"Scenes missing real locations: {scenes}"
        # Assert real props are extracted
        assert any("九阳神丹" in p or "斩龙神剑" in p for p in props), f"Props missing real items: {props}"
        # Assert chapters were mapped to episodes
        assert len(episodes) >= 2, f"Expected 2 episodes for 2 chapters, got: {len(episodes)}"
        assert any("重生坠仙谷" in e["title"] for e in episodes)
        assert any("踏入青云宗主殿" in e["title"] for e in episodes)
