"""Full-text evidence chunking for the explainer content chain (spec §C3.1).

The old planner handed the model a single evidence block and stopped at 24 000
characters, so the tail of a long document never reached any model call.  This
module replaces that truncation with a deterministic, resumable chunk plan:

* :func:`build_evidence_chunks` is **pure** — same spans in, same chunks out.
  Chunks are composed of whole ``source_span`` rows in stable source/offset
  order, and a single span body is never split (splitting one would make its
  ``source_span_id`` unquotable).
* coverage is decided by the program from ``owned_span_ids``; a fragment carried
  as ``context_only`` helps disambiguation and is *excluded* from coverage, so a
  neighbouring chunk cannot make the stage look complete and cannot create a
  duplicate piece of evidence.
* the manifest helpers persist per-chunk state to an atomically written
  ``analysis-manifest.json`` inside the task artifact directory, so a restart
  redoes only the chunks whose input hash changed or that never completed.
  There is no second queue and no vector store.

The module has no dependency on the repository, the model client or FastAPI.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from local_drama.domain.explainers.contracts import ExplainerContractError, content_hash, text_hash

__all__ = [
    "ANALYSIS_MANIFEST_FILENAME",
    "DEFAULT_CHUNK_CHARACTER_BUDGET",
    "DEFAULT_CONTEXT_CHARACTER_BUDGET",
    "MAX_CHUNKS",
    "build_evidence_chunks",
    "chunk_input_hash",
    "coverage_status",
    "empty_manifest",
    "mark_manifest_in_progress",
    "pending_chunks",
    "read_analysis_manifest",
    "record_chunk_result",
    "write_analysis_manifest",
]

#: §C3.1: a conservative starting body budget for one chunk.  It is a starting
#: configuration, not a performance promise: the template, entity summary, output
#: budget and safety margin must still fit the selected execution profile.
DEFAULT_CHUNK_CHARACTER_BUDGET = 12_000

#: How much of the previous chunk is repeated as ``context_only`` material.
DEFAULT_CONTEXT_CHARACTER_BUDGET = 2_000

#: A hard bound so a pathological document fails loudly instead of fanning out
#: into an unbounded number of model calls.
MAX_CHUNKS = 512

ANALYSIS_MANIFEST_FILENAME = "analysis-manifest.json"

_MANIFEST_SCHEMA_VERSION = "localdrama.explainer.analysis-manifest.v1"


def _span_sort_key(span: Mapping[str, Any]) -> tuple[str, int, int, str]:
    return (
        str(span.get("source_id") or ""),
        int(span.get("start_offset") or 0),
        int(span.get("ordinal") or 0),
        str(span.get("id") or ""),
    )


def _span_entry(span: Mapping[str, Any], *, context_only: bool) -> dict[str, Any]:
    return {
        "source_span_id": str(span.get("id") or span.get("source_span_id") or ""),
        "source_id": str(span.get("source_id") or ""),
        "quote_text": str(span.get("quote_text") or ""),
        "span_hash": str(span.get("span_hash") or text_hash(str(span.get("quote_text") or ""))),
        "start_offset": int(span.get("start_offset") or 0),
        "end_offset": int(span.get("end_offset") or 0),
        "context_only": bool(context_only),
    }


def build_evidence_chunks(
    spans: Sequence[Mapping[str, Any]],
    *,
    chunk_character_budget: int = DEFAULT_CHUNK_CHARACTER_BUDGET,
    context_character_budget: int = DEFAULT_CONTEXT_CHARACTER_BUDGET,
    max_chunks: int = MAX_CHUNKS,
) -> list[dict[str, Any]]:
    """Partition *spans* into whole-span chunks in stable original order.

    Guarantees, all of which the acceptance cases in §C9 depend on:

    * the union of every chunk's ``owned_span_ids`` is exactly the set of
      non-empty spans — no span is dropped and none is duplicated across
      ``owned_span_ids`` (a span may *also* appear as ``context_only`` in the
      next chunk, which never counts twice);
    * an ID and a single ``source_span`` body are never split;
    * the ordering is stable by ``(source_id, start_offset, ordinal, id)``;
    * ``chunk_id``/``chunk_hash``/``ordinal``/``owned_span_ids``/``context_span_ids``
      are assigned by the program, so the model can never decide which spans are
      "already processed".
    """

    if int(chunk_character_budget) <= 0 or int(context_character_budget) < 0:
        raise ExplainerContractError(
            "SCHEMA_INVALID",
            "分块预算必须是正整数",
            {
                "chunk_character_budget": chunk_character_budget,
                "context_character_budget": context_character_budget,
            },
        )
    ordered = sorted(
        (dict(span) for span in spans if str(span.get("quote_text") or "")),
        key=_span_sort_key,
    )

    owned_groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_source: str | None = None
    current_characters = 0
    for span in ordered:
        source_id = str(span.get("source_id") or "")
        length = len(str(span.get("quote_text") or ""))
        over_budget = current and current_characters + length > int(chunk_character_budget)
        source_changed = current and source_id != current_source
        if over_budget or source_changed:
            owned_groups.append(current)
            current = []
            current_characters = 0
        current.append(span)
        current_source = source_id
        current_characters += length
    if current:
        owned_groups.append(current)

    if len(owned_groups) > int(max_chunks):
        raise ExplainerContractError(
            "SCHEMA_INVALID",
            "全文分块数超过上限，请缩小选定章节范围或拆分资料后重试",
            {"chunk_count": len(owned_groups), "max_chunks": int(max_chunks)},
        )

    chunks: list[dict[str, Any]] = []
    for index, owned in enumerate(owned_groups):
        context_entries: list[dict[str, Any]] = []
        if index > 0 and int(context_character_budget) > 0:
            used = 0
            for span in reversed(owned_groups[index - 1]):
                length = len(str(span.get("quote_text") or ""))
                if used + length > int(context_character_budget):
                    break
                context_entries.insert(0, _span_entry(span, context_only=True))
                used += length
        owned_entries = [_span_entry(span, context_only=False) for span in owned]
        chunk = {
            "chunk_id": f"chk-{index + 1:04d}",
            "ordinal": index + 1,
            "owned_span_ids": [entry["source_span_id"] for entry in owned_entries],
            "context_span_ids": [entry["source_span_id"] for entry in context_entries],
            "spans": [*context_entries, *owned_entries],
            "source_ids": sorted({entry["source_id"] for entry in owned_entries}),
            "character_count": sum(len(entry["quote_text"]) for entry in owned_entries),
            "context_character_count": sum(len(entry["quote_text"]) for entry in context_entries),
        }
        chunk["chunk_hash"] = content_hash(
            {
                "chunk_id": chunk["chunk_id"],
                "ordinal": chunk["ordinal"],
                "owned": [
                    (entry["source_span_id"], entry["span_hash"]) for entry in owned_entries
                ],
                "context": [
                    (entry["source_span_id"], entry["span_hash"]) for entry in context_entries
                ],
            }
        )
        chunks.append(chunk)
    return chunks


def chunk_input_hash(
    chunk: Mapping[str, Any],
    *,
    prompt_version: str,
    contract: str,
    model_identity: str = "",
    scope: Mapping[str, Any] | None = None,
) -> str:
    """The resume key of one chunk: content + prompt/contract/model version.

    A chunk is redone exactly when this hash changes, so changing the prompt
    version or the selected chapter scope invalidates the affected chunks while
    an untouched re-run reuses every finished one (§C3.1).
    """

    return content_hash(
        {
            "chunk_hash": str(chunk.get("chunk_hash") or ""),
            "chunk_id": str(chunk.get("chunk_id") or ""),
            "owned_span_ids": [str(item) for item in (chunk.get("owned_span_ids") or [])],
            "prompt_version": prompt_version,
            "contract": contract,
            "model_identity": model_identity,
            "scope": dict(scope or {}),
        }
    )


def empty_manifest(*, required_span_ids: Iterable[Any], prompt_version: str, contract: str) -> dict[str, Any]:
    return {
        "schema_version": _MANIFEST_SCHEMA_VERSION,
        "prompt_version": prompt_version,
        "contract": contract,
        "required_span_ids": sorted({str(item) for item in required_span_ids}),
        "chunks": {},
        "status": "PENDING",
        "context_only_never_counted_in_coverage": True,
    }


def mark_manifest_in_progress(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Record that work started, so a crash leaves an honest "partial" state.

    A manifest that still says ``PENDING`` after two chunks were analysed would
    misreport real progress as "nothing done"; ``PARTIAL`` is the truthful state
    until the coverage check passes.
    """

    updated = json.loads(json.dumps(dict(manifest), ensure_ascii=False))
    updated["status"] = "PARTIAL"
    return updated


def pending_chunks(
    chunks: Sequence[Mapping[str, Any]],
    manifest: Mapping[str, Any] | None,
    *,
    prompt_version: str,
    contract: str,
    model_identity: str = "",
    scope: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Chunks that must actually be sent to the model on this run.

    A completed chunk whose input hash is unchanged is skipped; a chunk whose
    input hash changed, or that never completed, is redone.  This is what makes a
    restart lose neither the tail nor a duplicate piece of evidence.
    """

    records = dict((manifest or {}).get("chunks") or {})
    todo: list[dict[str, Any]] = []
    for chunk in chunks:
        record = records.get(str(chunk.get("chunk_id") or "")) or {}
        expected = chunk_input_hash(
            chunk,
            prompt_version=prompt_version,
            contract=contract,
            model_identity=model_identity,
            scope=scope,
        )
        if record.get("status") == "COMPLETED" and str(record.get("input_hash") or "") == expected:
            continue
        todo.append({**dict(chunk), "input_hash": expected})
    return todo


def coverage_status(
    chunks: Sequence[Mapping[str, Any]],
    manifest: Mapping[str, Any] | None,
    *,
    required_span_ids: Iterable[Any],
) -> dict[str, Any]:
    """Deterministic completion check: completed owned spans == required spans."""

    records = dict((manifest or {}).get("chunks") or {})
    completed: set[str] = set()
    for chunk in chunks:
        record = records.get(str(chunk.get("chunk_id") or "")) or {}
        if record.get("status") == "COMPLETED":
            completed.update(str(item) for item in (chunk.get("owned_span_ids") or []))
    required = {str(item) for item in required_span_ids}
    missing = sorted(required - completed)
    return {
        "required_span_ids": sorted(required),
        "completed_owned_span_ids": sorted(completed),
        "missing_span_ids": missing,
        "complete": not missing,
        "required_count": len(required),
        "completed_count": len(completed),
        "display": f"已分析 {len(completed)}/{len(required)} 段",
    }


def record_chunk_result(
    manifest: Mapping[str, Any],
    *,
    chunk: Mapping[str, Any],
    input_hash: str,
    response_hash: str,
    status: str = "COMPLETED",
    report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a new manifest with one chunk's state updated (pure)."""

    updated = json.loads(json.dumps(dict(manifest), ensure_ascii=False))
    updated.setdefault("chunks", {})
    updated["chunks"][str(chunk.get("chunk_id") or "")] = {
        "status": status,
        "input_hash": input_hash,
        "chunk_hash": str(chunk.get("chunk_hash") or ""),
        "owned_span_ids": [str(item) for item in (chunk.get("owned_span_ids") or [])],
        "context_span_ids": [str(item) for item in (chunk.get("context_span_ids") or [])],
        "response_hash": response_hash,
        "report": dict(report or {}),
    }
    return updated


def write_analysis_manifest(path: str | Path, payload: Mapping[str, Any]) -> str:
    """Atomically write the chunk manifest next to the stage's other artifacts."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    directory = str(target.parent)
    handle, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=directory)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(dict(payload), stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise
    return str(target)


def read_analysis_manifest(path: str | Path) -> dict[str, Any] | None:
    """Read a manifest, treating a corrupt/partial file as "nothing completed"."""

    target = Path(path)
    if not target.is_file():
        return None
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None
