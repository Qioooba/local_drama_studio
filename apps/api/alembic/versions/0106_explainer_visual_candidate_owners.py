"""Candidate owners, selection purpose scope and active-uniqueness repair.

Revision ID: 0106_explainer_visual_candidate_owners
Revises: 0105_explainer_beat_plan_scope

Design reference: 解说工厂-体验功能与代码改造实施规范 §D3 (表复用与最小迁移).

What this migration changes and why
----------------------------------
1. ``explainer_media_candidates`` gains an ``entity_id`` owner so character /
   scene / prop reference candidates live in the *same* table as beat keyframes
   and beat clips.  The design explicitly forbids inventing a fake beat to host
   an entity reference, so ``beat_id`` becomes nullable and exactly one of the
   two owners must be set.  Existing rows all carry a ``beat_id`` and stay valid
   without a rewrite.

2. ``explainer_media_candidates`` gains a nullable ``edition_id`` so a candidate
   can be attributed to the canvas / language version it was generated for.  A
   NULL value keeps the legacy "video-wide" meaning.

3. The old ``UNIQUE (beat_id, candidate_kind, variant_no)`` is replaced by
   **partial** unique indexes: SQLite treats NULLs as distinct in a plain UNIQUE
   constraint, so the previous rule neither constrained NULL-edition rows the way
   the repository intended nor could express "unique per owner *and* purpose".
   ``variant_no`` allocation in ``ExplainerStoryboardService`` is already scoped
   by ``(beat_id, candidate_kind)``, so adding ``purpose`` keeps every existing
   row unique.

4. ``explainer_beat_selections`` gains ``purpose NOT NULL DEFAULT 'VISUAL'`` and a
   partial unique index per ``(beat_id, purpose, edition_id)``.  Splitting
   ``KEYFRAME`` (the adopted first frame) from ``VISUAL`` (the final, composable
   clip) is required so adopting a new still cannot silently replace an adopted
   clip.  Existing rows are all ``VISUAL``.

   The migration first resolves any pre-existing multiple ACTIVE rows in the same
   scope, because the index cannot be created while duplicates exist.  The rule
   is deliberately conservative and reported: keep a human-locked row first, then
   a row whose media is still registered and renderable, then the newest one;
   everything else is superseded (never deleted), and any scope holding **two
   different human locks** is left untouched and reported as a conflict so a human
   decides — the design forbids guessing there.

5. ``run_identity_inputs`` / ``entity_identity_bindings`` had
   ``UNIQUE (..., status)`` which lets one ACTIVE row per target but forbids a
   second SUPERSEDED history row.  That blocks "generate references again" from
   the second round on, so both are replaced by ACTIVE-only partial unique
   indexes with the same effective ACTIVE guarantee.

SQLite has to rebuild a table to change its constraints.  ``beat_narration_links``
and ``explainer_beat_selections`` reference the rebuilt tables with ``ON DELETE
CASCADE``, so — following the repository convention in 0105/0062/0064/0066 — the
foreign-key pragma is disabled for the rebuild, the result is proven with
``PRAGMA foreign_key_check``, and enforcement is restored afterwards.
"""

from __future__ import annotations

import json

import sqlalchemy as sa

from alembic import op

revision = "0106_explainer_visual_candidate_owners"
down_revision = "0105_explainer_beat_plan_scope"
branch_labels = None
depends_on = None


CANDIDATE_PURPOSES = "IN ('REFERENCE','KEYFRAME','VISUAL','COMPOSITION','INFOGRAPHIC_LAYER','LICENSED_MEDIA')"


def upgrade() -> None:
    bind = op.get_bind()
    bind.commit()
    bind.exec_driver_sql("PRAGMA foreign_keys=OFF")
    bind.exec_driver_sql("PRAGMA legacy_alter_table=ON")
    try:
        _rebuild_candidates(bind)
        _rebuild_selections(bind)
        _rebuild_identity_tables(bind)
    finally:
        bind.exec_driver_sql("PRAGMA legacy_alter_table=OFF")

    bind.commit()
    violations = list(bind.exec_driver_sql("PRAGMA foreign_key_check"))
    if violations:
        raise RuntimeError(
            f"0106 explainer visual candidate owners produced foreign key violations: {violations[:10]}"
        )
    bind.exec_driver_sql("PRAGMA foreign_keys=ON")


def downgrade() -> None:
    bind = op.get_bind()
    bind.commit()
    bind.exec_driver_sql("PRAGMA foreign_keys=OFF")
    bind.exec_driver_sql("PRAGMA legacy_alter_table=ON")
    try:
        op.drop_index("uq_explainer_beat_selections_active_scope", table_name="explainer_beat_selections")
        op.drop_index("uq_explainer_beat_selections_active_global", table_name="explainer_beat_selections")

        op.drop_index("uq_explainer_media_candidates_beat_edition", table_name="explainer_media_candidates")
        op.drop_index("uq_explainer_media_candidates_beat_global", table_name="explainer_media_candidates")
        op.drop_index("uq_explainer_media_candidates_entity", table_name="explainer_media_candidates")
        op.drop_index("ix_explainer_media_candidates_entity", table_name="explainer_media_candidates")

        op.drop_index("uq_entity_identity_bindings_active_target", table_name="entity_identity_bindings")
        op.drop_index("uq_run_identity_inputs_active_entity", table_name="run_identity_inputs")

        # Return to the legacy shape: beat-owned candidates only.  Entity-owned
        # candidates cannot be represented by the old schema, so the rows are
        # deleted rather than silently re-attributed to an arbitrary beat.
        bind.exec_driver_sql("DELETE FROM explainer_media_candidates WHERE beat_id IS NULL")

        with op.batch_alter_table("explainer_media_candidates", recreate="always") as batch:
            batch.drop_column("edition_id")
            batch.drop_column("entity_id")
            batch.alter_column("beat_id", existing_type=sa.String(36), nullable=False)
            batch.create_unique_constraint(
                "uq_explainer_media_candidates_variant", ["beat_id", "candidate_kind", "variant_no"]
            )

        with op.batch_alter_table("explainer_beat_selections", recreate="always") as batch:
            batch.drop_column("purpose")

        with op.batch_alter_table("run_identity_inputs", recreate="always") as batch:
            batch.create_unique_constraint("uq_run_identity_inputs_entity", ["run_id", "entity_id", "status"])

        with op.batch_alter_table("entity_identity_bindings", recreate="always") as batch:
            batch.create_unique_constraint(
                "uq_entity_identity_bindings_target", ["video_id", "entity_id", "beat_id", "status"]
            )
    finally:
        bind.exec_driver_sql("PRAGMA legacy_alter_table=OFF")

    bind.commit()
    violations = list(bind.exec_driver_sql("PRAGMA foreign_key_check"))
    if violations:
        raise RuntimeError(f"0106 downgrade produced foreign key violations: {violations[:10]}")
    bind.exec_driver_sql("PRAGMA foreign_keys=ON")


def _rebuild_candidates(bind) -> None:
    with op.batch_alter_table("explainer_media_candidates", recreate="always") as batch:
        batch.drop_constraint("uq_explainer_media_candidates_variant", type_="unique")
        batch.add_column(sa.Column("entity_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("edition_id", sa.String(36), nullable=True))
        batch.alter_column("beat_id", existing_type=sa.String(36), nullable=True)
        batch.create_foreign_key(
            "fk_explainer_media_candidates_entity",
            "explainer_entities",
            ["entity_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_foreign_key(
            "fk_explainer_media_candidates_edition",
            "explainer_editions",
            ["edition_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_check_constraint(
            "ck_explainer_media_candidates_owner",
            "(beat_id IS NOT NULL AND entity_id IS NULL) OR (beat_id IS NULL AND entity_id IS NOT NULL)",
        )
        batch.create_check_constraint(
            "ck_explainer_media_candidates_purpose",
            f"purpose {CANDIDATE_PURPOSES}",
        )

    op.create_index(
        "uq_explainer_media_candidates_beat_global",
        "explainer_media_candidates",
        ["beat_id", "purpose", "candidate_kind", "variant_no"],
        unique=True,
        sqlite_where=sa.text("beat_id IS NOT NULL AND edition_id IS NULL"),
    )
    op.create_index(
        "uq_explainer_media_candidates_beat_edition",
        "explainer_media_candidates",
        ["beat_id", "purpose", "candidate_kind", "edition_id", "variant_no"],
        unique=True,
        sqlite_where=sa.text("beat_id IS NOT NULL AND edition_id IS NOT NULL"),
    )
    op.create_index(
        "uq_explainer_media_candidates_entity",
        "explainer_media_candidates",
        ["entity_id", "purpose", "candidate_kind", "variant_no"],
        unique=True,
        sqlite_where=sa.text("entity_id IS NOT NULL"),
    )
    op.create_index(
        "ix_explainer_media_candidates_entity",
        "explainer_media_candidates",
        ["entity_id", "status"],
        unique=False,
    )


def _rebuild_selections(bind) -> None:
    conflicts = _resolve_active_duplicates(bind)

    with op.batch_alter_table("explainer_beat_selections", recreate="always") as batch:
        batch.add_column(sa.Column("purpose", sa.String(32), nullable=False, server_default="VISUAL"))
        batch.create_check_constraint(
            "ck_explainer_beat_selections_purpose",
            f"purpose {CANDIDATE_PURPOSES}",
        )
        batch.create_check_constraint(
            "ck_explainer_beat_selections_lock_actor",
            "locked_by_human = 0 OR (actor IS NOT NULL AND length(trim(actor)) > 0)",
        )

    op.create_index(
        "uq_explainer_beat_selections_active_global",
        "explainer_beat_selections",
        ["beat_id", "purpose"],
        unique=True,
        sqlite_where=sa.text("status = 'ACTIVE' AND edition_id IS NULL"),
    )
    op.create_index(
        "uq_explainer_beat_selections_active_scope",
        "explainer_beat_selections",
        ["beat_id", "purpose", "edition_id"],
        unique=True,
        sqlite_where=sa.text("status = 'ACTIVE' AND edition_id IS NOT NULL"),
    )

    report = {
        "migration": revision,
        "resolved_active_conflicts": conflicts["resolved"],
        "unresolved_human_lock_conflicts": conflicts["unresolved"],
        "note": (
            "Scopes with two different human locks are preserved untouched; the affected beat keeps its rows "
            "readable and the application refuses new writes for that (beat, purpose, edition) scope until a "
            "human picks one, instead of the migration guessing."
        ),
    }
    bind.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS explainer_migration_reports ("
        "migration_revision TEXT PRIMARY KEY, payload_json TEXT NOT NULL, "
        "created_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    bind.exec_driver_sql(
        "INSERT OR REPLACE INTO explainer_migration_reports (migration_revision, payload_json) VALUES (?, ?)",
        (revision, json.dumps(report, ensure_ascii=False)),
    )


def _resolve_active_duplicates(bind) -> dict[str, list[dict[str, str]]]:
    """Collapse duplicate ACTIVE selections so the new partial index can be built.

    Priority per scope ``(beat_id, purpose, edition_id)``:
      1. a human-locked row (never superseded by a machine row);
      2. a row whose media version is still registered and readable;
      3. the newest ``decided_at``.
    Two or more *different* human locks in one scope is a genuine conflict: the
    rows are left exactly as they are and reported for a human decision.
    """

    rows = list(
        _query_all(
            bind,
            "SELECT s.id, s.beat_id, s.edition_id, s.locked_by_human, s.decided_at, s.media_version_id, "
            "       (SELECT COUNT(1) FROM media_versions mv WHERE mv.id = s.media_version_id) AS media_present "
            "  FROM explainer_beat_selections s "
            " WHERE s.status = 'ACTIVE'",
        )
    )
    scopes: dict[tuple[str, str | None], list] = {}
    for row in rows:
        key = (row[1], row[2])
        scopes.setdefault(key, []).append(row)

    resolved: list[dict[str, str]] = []
    unresolved: list[dict[str, str]] = []
    for (beat_id, edition_id), group in scopes.items():
        if len(group) < 2:
            continue
        locked = [row for row in group if int(row[3] or 0)]
        if len(locked) > 1:
            unresolved.append(
                {
                    "beat_id": beat_id,
                    "edition_id": edition_id or "",
                    "selection_ids": ",".join(sorted(str(row[0]) for row in locked)),
                }
            )
            continue
        ordered = sorted(
            group,
            key=lambda row: (
                -(1 if int(row[3] or 0) else 0),  # a human lock wins
                -(1 if int(row[6] or 0) else 0),  # a still-registered media version wins
                -len(str(row[4] or "")),  # newest decided_at (ISO text) wins
                str(row[4] or ""),
                str(row[0]),
            ),
        )
        keep = ordered[0]
        for row in ordered[1:]:
            _execute(
                bind,
                "UPDATE explainer_beat_selections SET status = 'SUPERSEDED' WHERE id = ?",
                (row[0],),
            )
            resolved.append(
                {
                    "beat_id": beat_id,
                    "edition_id": edition_id or "",
                    "kept_selection_id": str(keep[0]),
                    "superseded_selection_id": str(row[0]),
                }
            )
    return {"resolved": resolved, "unresolved": unresolved}


def _query_all(bind, sql: str, parameters: tuple = ()) -> list:
    """Run a read statement on either an Alembic bind or a plain sqlite3 connection."""

    if hasattr(bind, "exec_driver_sql"):
        return list(bind.exec_driver_sql(sql, parameters))
    return list(bind.execute(sql, parameters).fetchall())


def _execute(bind, sql: str, parameters: tuple = ()) -> None:
    """Run a write statement on either an Alembic bind or a plain sqlite3 connection."""

    if hasattr(bind, "exec_driver_sql"):
        bind.exec_driver_sql(sql, parameters)
        return
    bind.execute(sql, parameters)


def _rebuild_identity_tables(bind) -> None:
    with op.batch_alter_table("run_identity_inputs", recreate="always") as batch:
        batch.drop_constraint("uq_run_identity_inputs_entity", type_="unique")

    op.create_index(
        "uq_run_identity_inputs_active_entity",
        "run_identity_inputs",
        ["run_id", "entity_id"],
        unique=True,
        sqlite_where=sa.text("status = 'ACTIVE'"),
    )

    with op.batch_alter_table("entity_identity_bindings", recreate="always") as batch:
        batch.drop_constraint("uq_entity_identity_bindings_target", type_="unique")

    op.create_index(
        "uq_entity_identity_bindings_active_target",
        "entity_identity_bindings",
        ["video_id", "entity_id", "beat_id", "edition_id"],
        unique=True,
        sqlite_where=sa.text("status = 'ACTIVE'"),
    )
