"""Read-only local UAT diagnostic for Ollama source-passage alignment.

This intentionally prints only source-passage metadata and short quotes.  It
does not persist a draft or change any production state.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from local_drama.application.local_llm import (
    _BREAKDOWN_RESPONSE_SCHEMA,
    _numbered_source_paragraphs,
    _profile_runtime_contract,
)
from local_drama.config import Settings
from local_drama.infrastructure.database.sqlite import Database
from local_drama.infrastructure.local_llm import LocalLLMClient

PROJECT_ID = "c60586d6-d554-49a9-9e4d-6ba43c65a602"
JOB_ID = "ff0b9e4c-92a0-4f7b-94c9-93dee3e5303a"


def main() -> None:
    settings = Settings.from_env()
    database = Database(settings.database_path)
    with database.connect() as connection:
        row = connection.execute(
            """SELECT p.code,sdv.extracted_text_rel,epv.capability_json
            FROM jobs j
            JOIN projects p ON p.id=j.project_id
            JOIN source_document_versions sdv
              ON sdv.id=json_extract(j.input_snapshot_json,'$.source_document_version_id')
            JOIN execution_profile_versions epv ON epv.id=j.execution_profile_version_id
            WHERE j.id=? AND j.project_id=?""",
            (JOB_ID, PROJECT_ID),
        ).fetchone()
    if row is None:
        raise SystemExit("UAT job snapshot not found")
    source_path = (
        settings.projects_root / str(row["code"]) / str(row["extracted_text_rel"])
    ).resolve()
    source_text = source_path.read_text(encoding="utf-8")
    numbered_source_text, paragraph_offsets = _numbered_source_paragraphs(source_text)
    capability = json.loads(str(row["capability_json"] or "{}"))
    contract = _profile_runtime_contract(capability, settings.llm_base_url)
    output = LocalLLMClient(
        contract["base_url"],
        contract["model"],
        provider=contract["provider"],
    ).chat_json(
        "你是本地剧本拆解器。最终答案只输出 JSON 对象，顶层必须包含 scenes、confidence、questions、source_passages。"
        "每个 scene 必须包含 scene_no、title、summary、characters、shots；每个 shot 必须包含 shot_no、visual、action、dialogue、duration_seconds。"
        "confidence 必须是 {overall:0到1,notes:字符串数组}；questions 是待人工确认的字符串数组；"
        "输入原文的每个非空段落都有 P001、P002 等稳定编号。source_passages 是 {scene_no,paragraph_no} 数组，每个场次至少一条；paragraph_no 必须直接取自该场次所依据的原文 P 编号，不要返回 quote，不要编造编号。P 编号只用于引用，不得写进场景正文、镜头或对白。不得臆造原文不存在的关键事实。",
        numbered_source_text,
        json_schema=_BREAKDOWN_RESPONSE_SCHEMA,
    )
    items = []
    for index, passage in enumerate(output.get("source_passages") or [], start=1):
        paragraph_no = passage.get("paragraph_no")
        paragraph_range = paragraph_offsets.get(paragraph_no) if isinstance(paragraph_no, int) else None
        quote = source_text[slice(*paragraph_range)] if paragraph_range else ""
        items.append(
            {
                "index": index,
                "scene_no": passage.get("scene_no"),
                "paragraph_no": paragraph_no,
                "valid_paragraph": paragraph_range is not None,
                "exact_match": bool(quote) and source_text.find(quote) >= 0,
                "quote_length": len(quote),
                "quote_preview": quote[:160],
            }
        )
    print(json.dumps({"scene_count": len(output.get("scenes") or []), "passages": items}, ensure_ascii=True))


if __name__ == "__main__":
    main()
