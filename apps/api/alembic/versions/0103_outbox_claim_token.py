"""outbox delivery claim ownership

Adds ``claim_token`` to ``outbox_delivery_attempts``.

The delivery ledger decided ownership purely from ``status``: an event in
``ATTEMPTING`` was assumed to belong to the process that observed it.  That is not
ownership — a second dispatcher may have reclaimed the row after a lease expiry,
and the first dispatcher's late acknowledgement/failure/release then wrote the
*new* owner's ledger.  A ``claim_token`` makes every begin/ack/failure/release a
compare-and-set on the token this dispatcher was actually granted.

Revision ID: 0103_outbox_claim_token
Revises: 0102_explainer_factory_foundation
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0103_outbox_claim_token"
down_revision: str | None = "0102_explainer_factory_foundation"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "outbox_delivery_attempts",
        sa.Column("claim_token", sa.String(64), nullable=True),
    )
    # Every existing claim is unresolvable (it belonged to a process that is not
    # running any more), so the token stays NULL and no old row can match a new one.


def downgrade() -> None:
    op.drop_column("outbox_delivery_attempts", "claim_token")
