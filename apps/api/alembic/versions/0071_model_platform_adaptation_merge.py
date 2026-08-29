"""Merge the concurrent adaptation-planning and Model Platform V2 heads.

Revision ID: 0071_model_platform_adaptation_merge
Revises: 0070_adaptation_planning_foundation, 0070_model_platform_v2_foundation
"""

from __future__ import annotations

revision = "0071_model_platform_adaptation_merge"
down_revision = ("0070_adaptation_planning_foundation", "0070_model_platform_v2_foundation")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """The two parent revisions create disjoint tables; this is graph-only."""


def downgrade() -> None:
    """Downgrade is handled by Alembic traversing each parent revision."""

