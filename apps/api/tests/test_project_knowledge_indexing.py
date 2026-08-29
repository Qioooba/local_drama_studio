from __future__ import annotations

import hashlib

from local_drama.model_platform.application.project_knowledge_indexing import _batches, _chunks


def test_project_knowledge_chunks_preserve_source_offsets_hashes_and_batch_limit() -> None:
    text = f"第一段\n\n{'甲' * 8200}\n\n第三段"

    chunks = _chunks(text)

    assert [item["ordinal"] for item in chunks] == [1, 2, 3, 4]
    assert chunks[0]["text"] == "第一段"
    assert chunks[1]["text"] == "甲" * 8192
    assert chunks[2]["text"] == "甲" * 8
    assert chunks[2]["source_start"] == chunks[1]["source_end"]
    assert chunks[2]["text_sha256"] == hashlib.sha256(("甲" * 8).encode("utf-8")).hexdigest()
    assert chunks[3]["text"] == "第三段"
    assert chunks[3]["source_start"] > chunks[2]["source_end"]

    batches = _batches(chunks * 9)
    assert [len(batch) for batch in batches] == [32, 4]
    assert all(len(batch) <= 32 for batch in batches)
