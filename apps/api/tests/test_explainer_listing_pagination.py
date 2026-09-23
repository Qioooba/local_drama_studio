"""Explainer listing: keyset pagination, server-side search, bounded aggregates.

Reproduced defects on the audit snapshot (EXP-07 / LDS-14):

* ``list_explainers`` declared a ``cursor`` parameter but the SQL never used it and
  ``next_cursor`` was hard-coded ``None``, so with 101 projects and ``limit=100``
  the 101st was unreachable and re-sending the 100th project's id as the cursor
  returned the same first page;
* ``edition_count`` and ``open_issue_count`` were computed with two queries per
  row (~1 + 2N for a page), and the issue count fetched every id and took ``len``;
* there was no server-side search at all, so the browser could only filter the
  rows it had already loaded.
"""

from __future__ import annotations

import asyncio
import hashlib

import pytest

from local_drama.api.routes.explainers import (
    _decode_list_cursor,
    _encode_list_cursor,
    _list_filter_digest,
    list_explainers,
)
from local_drama.domain.explainers.contracts import ExplainerContractError
from local_drama.errors import ApiError
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository
from local_drama.infrastructure.database.sqlite import Database


class _FakeRequest:
    def __init__(self, database: Database) -> None:
        self.app = type("App", (), {"state": type("State", (), {"database": database})()})()


def _seed_projects(database: Database, count: int, *, updated_at: str = "2026-01-01T00:00:00Z") -> list[str]:
    ids: list[str] = []
    with database.transaction() as connection:
        for index in range(count):
            project_id = f"proj-{index:04d}"
            ids.append(project_id)
            connection.execute(
                """INSERT INTO projects (id, code, title, status, template_version, root_rel,
                target_duration_ms, product_kind, created_at, updated_at, created_by)
                VALUES (?, ?, ?, 'DRAFT', 'v2', ?, 300000, 'EXPLAINER', ?, ?, 'test')""",
                (project_id, f"EXP-{index:04d}", f"作品 {index:03d} 不可遗漏" if index >= 100 else f"作品 {index:03d}", project_id, updated_at, updated_at),
            )
            connection.execute(
                "INSERT INTO explainer_videos (id, project_id, title, topic, content_kind, source_locale,"
                " input_kind, duration_mode, target_seconds, tolerance_percent, automation_mode,"
                " inference_mode, research_mode, status)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"video-{index:04d}", project_id, "作品", "主题", "FACTUAL_EXPLAINER", "zh-CN",
                    "TOPIC", "TARGET", 300, 5.0, "AUTO_WITH_EXCEPTIONS", "LOCAL_ONLY",
                    "OFFLINE_IMPORT", "DRAFT",
                ),
            )
    return ids


def _page(
    database: Database,
    *,
    limit: int = 50,
    cursor: str | None = None,
    search: str | None = None,
    project_kind: str = "EXPLAINER",
) -> dict[str, object]:
    """Call the real async route function from a synchronous test."""

    return asyncio.run(
        list_explainers(
            _FakeRequest(database),
            project_kind=project_kind,
            limit=limit,
            cursor=cursor,
            search=search,
        )
    )


# --------------------------------------------------------------------------- #
# EXP-07: the tail of a long list is reachable
# --------------------------------------------------------------------------- #
def test_101_projects_page_as_100_then_1_with_no_duplicates_or_gaps(database: Database) -> None:
    _seed_projects(database, 101)
    first = _page(database, limit=100)
    assert first["loaded_count"] == 100
    assert first["has_more"] is True
    assert first["next_cursor"]

    second = _page(database, limit=100, cursor=first["next_cursor"])
    assert second["loaded_count"] == 1
    assert second["has_more"] is False
    assert second["next_cursor"] is None

    collected = [str(item["project_id"]) for item in first["items"]] + [
        str(item["project_id"]) for item in second["items"]
    ]
    assert len(collected) == 101
    assert len(set(collected)) == 101


def test_1000_projects_are_all_reachable_page_by_page(database: Database) -> None:
    _seed_projects(database, 1000)
    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        page = _page(database, limit=137, cursor=cursor)
        seen.extend(str(item["project_id"]) for item in page["items"])
        pages += 1
        cursor = page["next_cursor"]
        if cursor is None:
            break
        assert pages < 20, "分页没有收敛"
    assert len(seen) == 1000
    assert len(set(seen)) == 1000
    assert pages == 8  # ceil(1000 / 137)


def test_search_finds_a_project_beyond_the_first_page(database: Database) -> None:
    _seed_projects(database, 105)
    page = _page(database, limit=100, search="不可遗漏")
    titles = [str(item["title"]) for item in page["items"]]
    assert page["loaded_count"] == 5
    assert all("不可遗漏" in title for title in titles)
    assert any("104" in title for title in titles)


def test_search_matches_the_project_code_and_is_a_literal_search(database: Database) -> None:
    _seed_projects(database, 5)
    by_code = _page(database, search="EXP-0002")
    assert [str(item["project_id"]) for item in by_code["items"]] == ["proj-0002"]
    # A LIKE wildcard in the query must not become a wildcard.
    wildcard = _page(database, search="%")
    assert wildcard["items"] == []


def test_identical_updated_at_still_pages_by_the_id_tiebreaker(database: Database) -> None:
    _seed_projects(database, 5, updated_at="2026-02-02T02:02:02Z")
    first = _page(database, limit=2)
    second = _page(database, limit=2, cursor=first["next_cursor"])
    third = _page(database, limit=2, cursor=second["next_cursor"])
    seen = [
        str(item["project_id"])
        for page in (first, second, third)
        for item in page["items"]
    ]
    assert len(seen) == 5
    assert len(set(seen)) == 5


def test_last_page_and_empty_page_are_stable(database: Database) -> None:
    _seed_projects(database, 3)
    only = _page(database, limit=10)
    assert only["loaded_count"] == 3
    assert only["has_more"] is False
    empty = _page(database, limit=10, search="不存在的标题")
    assert empty["items"] == []
    assert empty["has_more"] is False
    assert empty["next_cursor"] is None


# --------------------------------------------------------------------------- #
# the cursor is validated, not trusted
# --------------------------------------------------------------------------- #
def test_a_tampered_cursor_is_refused(database: Database) -> None:
    _seed_projects(database, 4)
    page = _page(database, limit=2)
    cursor = str(page["next_cursor"])
    tampered = ("A" if cursor[0] != "A" else "B") + cursor[1:]
    with pytest.raises(ApiError) as error:
        _page(database, limit=2, cursor=tampered)
    assert error.value.code == "INVALID_CURSOR"


def test_a_cursor_from_other_filters_is_refused(database: Database) -> None:
    _seed_projects(database, 4)
    page = _page(database, limit=2)
    with pytest.raises(ApiError) as error:
        _page(database, limit=2, cursor=page["next_cursor"], search="作品")
    assert error.value.code == "INVALID_CURSOR"


def test_a_garbage_cursor_is_refused(database: Database) -> None:
    _seed_projects(database, 4)
    for value in ("zzzz", "!!!!", "a"):
        with pytest.raises(ApiError) as error:
            _page(database, limit=2, cursor=value)
        assert error.value.code == "INVALID_CURSOR"


def test_cursor_helpers_round_trip_and_reject_foreign_digests() -> None:
    digest = _list_filter_digest(project_kind="EXPLAINER", search="x")
    cursor = _encode_list_cursor(
        updated_at="2026-01-01T00:00:00Z", project_id="proj-1", filter_digest=digest
    )
    assert _decode_list_cursor(cursor, filter_digest=digest) == ("2026-01-01T00:00:00Z", "proj-1")
    other = _list_filter_digest(project_kind="EXPLAINER", search="y")
    with pytest.raises(ExplainerContractError):
        _decode_list_cursor(cursor, filter_digest=other)


def test_filter_digest_changes_with_every_filter_field() -> None:
    base = _list_filter_digest(project_kind="EXPLAINER", search=None)
    assert base != _list_filter_digest(project_kind="DRAMA", search=None)
    assert base != _list_filter_digest(project_kind="EXPLAINER", search="a")
    assert base == _list_filter_digest(project_kind="EXPLAINER", search="  ")


# --------------------------------------------------------------------------- #
# aggregates are bounded and correct
# --------------------------------------------------------------------------- #
def test_counts_come_from_two_bounded_aggregates(database: Database) -> None:
    _seed_projects(database, 6)
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO explainer_editions (id, video_id, edition_key, voice_locale, status)"
            " VALUES (?,?,?,?,?)",
            ("ed-1", "video-0002", "main", "zh-CN", "READY"),
        )
        connection.execute(
            "INSERT INTO explainer_editions (id, video_id, edition_key, voice_locale, status)"
            " VALUES (?,?,?,?,?)",
            ("ed-2", "video-0002", "alt", "en-US", "READY"),
        )
        connection.execute(
            "INSERT INTO explainer_editions (id, video_id, edition_key, voice_locale, status)"
            " VALUES (?,?,?,?,?)",
            ("ed-3", "video-0003", "main", "zh-CN", "READY"),
        )
    page = _page(database, limit=10)
    assert page["aggregate_query_count"] == 2
    by_id = {str(item["project_id"]): item for item in page["items"]}
    assert by_id["proj-0002"]["edition_count"] == 2
    assert by_id["proj-0003"]["edition_count"] == 1
    assert by_id["proj-0000"]["edition_count"] == 0
    for item in page["items"]:
        assert item["open_issue_count"] == 0
        assert item["episode_count"] == 0


def test_query_count_does_not_grow_with_the_page_size(database: Database) -> None:
    """The page must not be ``1 + 2N`` queries any more.

    ``ExplainerRepository`` is counted directly: three statements for a page of any
    size (the page itself plus two aggregates), and zero when the page is empty.
    """

    _seed_projects(database, 30)
    counted: list[str] = []
    original = ExplainerRepository.query_all

    def spy(self: ExplainerRepository, sql: str, parameters: object = ()) -> object:
        counted.append(" ".join(sql.split()))
        return original(self, sql, parameters)  # type: ignore[arg-type]

    ExplainerRepository.query_all = spy  # type: ignore[assignment]
    try:
        _page(database, limit=5)
        small = len(counted)
        counted.clear()
        _page(database, limit=25)
        large = len(counted)
        counted.clear()
        _page(database, limit=5, search="不存在的标题")
        empty = len(counted)
    finally:
        ExplainerRepository.query_all = original  # type: ignore[assignment]
    assert small == large == 3, (small, large)
    assert empty == 1


def test_repository_never_loads_every_issue_id_for_a_count(database: Database) -> None:
    source = open("apps/api/local_drama/api/routes/explainers.py", encoding="utf-8").read()
    assert "COUNT(*) AS n FROM explainer_qc_issues" in source
    assert "SELECT i.id FROM explainer_qc_issues" not in source


def test_drama_projects_are_not_listed_as_explainers(database: Database) -> None:
    _seed_projects(database, 2)
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO projects (id, code, title, status, template_version, root_rel,
            target_duration_ms, product_kind, created_at, updated_at, created_by)
            VALUES ('drama-1', 'DRAMA-1', '短剧', 'DRAFT', 'v2', 'drama-1', 300000, 'DRAMA',
            '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'test')"""
        )
    page = _page(database, limit=10)
    assert all(str(item["product_kind"]) == "EXPLAINER" for item in page["items"])
    assert "drama-1" not in {str(item["project_id"]) for item in page["items"]}

