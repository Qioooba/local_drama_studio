"""Real-model audit of the explainer text chain (content extract / script / storyboard).

What this does
--------------
It creates a throw-away instance (its own data/projects/work roots, so the user's
database is untouched), then for every article in the corpus:

1. creates the workspace through the production creation command
   (``build_explainer_creation_service``) — the same command the HTTP route uses;
2. records the pasted article as a source with spans through
   ``ExplainerResearchService``;
3. runs the **real stage handlers** from ``build_stage_handlers`` for
   ``FACT_EXTRACT`` → ``NARRATION_WRITE`` → ``EXPLAINER_STORYBOARD``;
4. writes one JSON per article containing every prompt sent to the model, every raw
   response, the applied rows (entities/claims/events/segments/beats) and the stage
   summaries.

Nothing is mocked: the model is the configured local runtime (Ollama by default) and
the handlers are the ones the worker registers.  A stage that fails is recorded with
its real error instead of being retried into a fake success.

    python scripts/audit_explainer_llm.py --articles 01,02 --stages FACT_EXTRACT
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from scripts.migrate import migrate  # noqa: E402

from local_drama.application.explainers.commands import (  # noqa: E402
    ExplainerCreateCommand,
    build_explainer_creation_service,
)
from local_drama.application.explainers.research import ExplainerResearchService  # noqa: E402
from local_drama.application.explainers.runtime_adapters import build_planner_factory  # noqa: E402
from local_drama.application.explainers.text_planner import build_stage_handlers  # noqa: E402
from local_drama.config import Settings  # noqa: E402
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository  # noqa: E402
from local_drama.infrastructure.database.sqlite import Database  # noqa: E402

DEFAULT_CORPUS = REPO_ROOT / "docs" / "解说工厂" / "llm-审计" / "corpus.json"
DEFAULT_OUT = REPO_ROOT / "artifacts" / "llm-audit"
DEFAULT_INSTANCE = REPO_ROOT / ".llm-audit-instance"


class RecordingClient:
    """Wrap the real LLM client and record every request/response pair."""

    def __init__(self, inner: Any, sink: list[dict[str, Any]], label: str) -> None:
        self._inner = inner
        self._sink = sink
        self._label = label
        self.model = getattr(inner, "model", None)
        self.provider = getattr(inner, "provider", None)

    def chat_json(self, system: str, user: str, images: Any = None, **kwargs: Any) -> dict[str, Any]:
        started = time.time()
        record: dict[str, Any] = {
            "stage": self._label,
            "system": system,
            "user": user,
            "inference_options": kwargs.get("inference_options") or {},
            "schema": kwargs.get("json_schema"),
        }
        try:
            result = self._inner.chat_json(system, user, images, **kwargs)
        except Exception as error:  # noqa: BLE001 - the audit records real failures
            record.update(
                {
                    "seconds": round(time.time() - started, 1),
                    "error": f"{type(error).__name__}: {error}",
                    "error_code": getattr(error, "code", None),
                }
            )
            self._sink.append(record)
            raise
        record.update({"seconds": round(time.time() - started, 1), "response": result})
        self._sink.append(record)
        return result


def _settings(instance: Path) -> Settings:
    return Settings(
        data_root=instance / "data",
        projects_root=instance / "projects",
        work_root=instance / "work",
        cache_root=instance / "cache",
        logs_root=instance / "logs",
        backups_root=instance / "backups",
        # The local model, never a remote provider: the audit must not send the
        # corpus anywhere off this machine.
        llm_provider="OLLAMA_LOOPBACK",
        llm_base_url="http://127.0.0.1:11434",
        llm_model=None,
    )


def _load_corpus(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return list(payload["articles"])


def _client_for(database: Database, settings: Settings, sink: list[dict[str, Any]], label: str) -> Any:
    from local_drama.application.local_llm import LocalLLMService

    def factory() -> Any:
        inner = LocalLLMService(database, settings).client(model=settings.llm_model)
        return RecordingClient(inner, sink, label)

    return factory


def _stage_payload(stage: str, project_id: str, video_id: str, packet_id: str, render_types: list[str]) -> dict[str, Any]:
    payload: dict[str, Any] = {"project_id": project_id, "video_id": video_id, "packet_id": packet_id}
    if stage == "EXPLAINER_STORYBOARD":
        payload["usable_render_types"] = render_types
    return payload


def run_article(
    article: dict[str, Any],
    *,
    instance: Path,
    settings: Settings,
    stages: list[str],
    out_dir: Path,
    model: str | None,
) -> dict[str, Any]:
    database = Database(settings.database_path)
    calls: list[dict[str, Any]] = []

    creation = build_explainer_creation_service(database, settings)
    command = ExplainerCreateCommand(
        title=article["title"],
        topic=article.get("topic") or article["title"],
        content_kind=article.get("content_kind", "FACTUAL_EXPLAINER"),
        input_kind="PASTED_SCRIPT",
        script_policy=article.get("script_policy", "ADAPT_SOURCES"),
        pasted_text=article["text"],
        duration_mode="TARGET",
        target_seconds=int(article.get("target_seconds", 90)),
        source_locale="zh-CN",
        automation_mode="MANUAL_REVIEW",
        inference_mode="LOCAL_ONLY",
        research_mode="OFFLINE_IMPORT",
    )
    created = creation.create_workspace(command, idempotency_key=f"audit-{article['id']}")
    project_id = str(created["project"]["id"])
    video_id = str(created["video"]["id"])

    with database.transaction() as connection:
        repo = ExplainerRepository(connection)
        research = ExplainerResearchService(repo)
        packet = research.create_packet(
            project_id=project_id,
            video_id=video_id,
            mode="OFFLINE_IMPORT",
            topic=article.get("topic") or article["title"],
        )
        packet_id = str(packet["id"])
        source = research.import_document(
            project_id=project_id,
            video_id=video_id,
            packet_id=packet_id,
            text=article["text"],
            title=article["title"],
            source_kind="DOCUMENT_IMPORT",
            language="zh-CN",
            rights={"credibility_kind": "USER_SUPPLIED"},
        )

    def repo_factory() -> Any:
        import contextlib

        @contextlib.contextmanager
        def _open() -> Any:
            connection = database.connect()
            try:
                yield ExplainerRepository(connection)
            finally:
                connection.close()

        return _open()

    handler_calls: list[dict[str, Any]] = []
    planner_factory = build_planner_factory(database, settings)

    # Each handler resolves its own client; wrap the factory so the recorder sees
    # the exact system/user text the product sends for that stage.
    def recording_planner_factory(label: str) -> Any:
        def factory() -> Any:
            planner = planner_factory()
            planner._client_factory = _client_for(database, settings, calls, label)  # type: ignore[attr-defined]
            return planner

        return factory

    render_types = article.get("usable_render_types") or ["STILL_MOTION", "I2V", "INFOGRAPHIC", "LICENSED_MEDIA"]
    results: dict[str, Any] = {}
    for stage in stages:
        output_root = instance / "work" / "audit" / article["id"] / stage
        output_root.mkdir(parents=True, exist_ok=True)
        handlers = build_stage_handlers(
            planner_factory=recording_planner_factory(stage),
            repo_factory=repo_factory,
            read_repo_factory=repo_factory,
        )
        context = {
            "semantic_inputs": _stage_payload(stage, project_id, video_id, packet_id, render_types),
            "output_root": output_root,
        }
        started = time.time()
        try:
            outcome = handlers[stage]({}, context)
            results[stage] = {
                "status": outcome.get("status"),
                "summary": outcome.get("summary"),
                "produced": outcome.get("produced"),
                "seconds": round(time.time() - started, 1),
            }
        except Exception as error:  # noqa: BLE001 - a failing stage is audit evidence
            results[stage] = {
                "status": "ERROR",
                "error": f"{type(error).__name__}: {error}",
                "error_code": getattr(error, "code", None),
                "details": getattr(error, "details", None),
                "traceback": traceback.format_exc()[-2000:],
                "seconds": round(time.time() - started, 1),
            }

    with database.connect() as connection:
        repo = ExplainerRepository(connection)
        entities = repo.list_where("explainer_entities", {"video_id": video_id}, order_by="code", descending=False)
        claims = repo.list_where("explainer_claims", {"video_id": video_id}, order_by="code", descending=False)
        events = repo.list_where("explainer_events", {"video_id": video_id}, order_by="created_at", descending=False)
        video = repo.get("explainer_videos", video_id)
        script_revision_id = str(video.get("current_script_revision_id") or "")
        segments = repo.segments(script_revision_id) if script_revision_id else []
        beats = repo.beats(video_id)
        sources = repo.list_where("explainer_sources", {"video_id": video_id})
        spans = [
            dict(row)
            for row in repo.query_all(
                "SELECT id, source_id, ordinal, start_offset, end_offset, quote_text, span_hash "
                "FROM explainer_source_spans WHERE source_id = ? ORDER BY ordinal",
                (str(source["id"]),),
            )
        ]

    payload = {
        "article": {
            "id": article["id"],
            "style": article.get("style"),
            "title": article["title"],
            "content_kind": article.get("content_kind"),
            "script_policy": article.get("script_policy"),
            "target_seconds": article.get("target_seconds"),
            "characters": len(article["text"]),
        },
        "ids": {"project_id": project_id, "video_id": video_id, "packet_id": packet_id},
        "model": model,
        "stages": results,
        "source": {
            "body_sha256": source.get("body_sha256"),
            "span_count": len(spans),
            "spans": spans,
        },
        "applied": {
            "entity_count": len(entities),
            "entities": entities,
            "claim_count": len(claims),
            "claims": claims,
            "event_count": len(events),
            "events": events,
            "script_revision_id": script_revision_id or None,
            "segment_count": len(segments),
            "segments": segments,
            "beat_count": len(beats),
            "beats": beats,
        },
        "calls": calls,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{article['id']}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--instance", type=Path, default=DEFAULT_INSTANCE)
    parser.add_argument("--articles", default="", help="comma separated article ids (default: all)")
    parser.add_argument(
        "--stages",
        default="FACT_EXTRACT,NARRATION_WRITE,EXPLAINER_STORYBOARD",
        help="comma separated stage codes to run",
    )
    parser.add_argument("--model", default="qwen3.8:27b")
    parser.add_argument("--reset", action="store_true", help="delete the instance database first")
    args = parser.parse_args()

    articles = _load_corpus(args.corpus)
    wanted = {item.strip() for item in args.articles.split(",") if item.strip()}
    if wanted:
        articles = [item for item in articles if item["id"] in wanted or item["id"].split("_")[0] in wanted]
    stages = [item.strip() for item in args.stages.split(",") if item.strip()]

    instance = args.instance
    if args.reset and instance.exists():
        # The derived project code is stable, so a stale project directory would make
        # the creation command refuse the run: reset means "start from nothing".
        import shutil

        shutil.rmtree(instance, ignore_errors=True)
    for name in ("data", "projects", "work", "cache", "logs", "backups"):
        (instance / name).mkdir(parents=True, exist_ok=True)

    settings = _settings(instance)
    settings.llm_model = args.model
    migrate(settings.database_path)

    summary: list[dict[str, Any]] = []
    for article in articles:
        print(f"=== {article['id']} [{article.get('style')}] {article['title']}", flush=True)
        started = time.time()
        try:
            payload = run_article(
                article,
                instance=instance,
                settings=settings,
                stages=stages,
                out_dir=args.out,
                model=args.model,
            )
            entry = {
                "id": article["id"],
                "style": article.get("style"),
                "seconds": round(time.time() - started, 1),
                "model_calls": len(payload["calls"]),
                "entities": payload["applied"]["entity_count"],
                "claims": payload["applied"]["claim_count"],
                "events": payload["applied"]["event_count"],
                "segments": payload["applied"]["segment_count"],
                "beats": payload["applied"]["beat_count"],
                "stages": {key: value.get("status") for key, value in payload["stages"].items()},
            }
        except Exception as error:  # noqa: BLE001
            entry = {
                "id": article["id"],
                "style": article.get("style"),
                "seconds": round(time.time() - started, 1),
                "fatal": f"{type(error).__name__}: {error}",
                "traceback": traceback.format_exc()[-1500:],
            }
        summary.append(entry)
        print(json.dumps(entry, ensure_ascii=False), flush=True)

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("summary written:", args.out / "summary.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
