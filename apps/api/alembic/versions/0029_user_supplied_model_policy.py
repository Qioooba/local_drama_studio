"""Reclassify user-supplied model license absence as a non-blocking risk.

Revision ID: 0029_user_supplied_model_policy
Revises: 0028_audio_binding_authority
"""

from alembic import op

revision = "0029_user_supplied_model_policy"
down_revision = "0028_audio_binding_authority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """UPDATE model_compatibility_reports
        SET report_status='PASS', blockers_json='[]'
        WHERE json_extract(quantization_json, '$.status')='HEADER_MATCHED'
        AND report_status='BLOCKED'
        AND blockers_json IN (
            '["LICENSE_EVIDENCE_MISSING","FORMAL_IMPORT_REQUIRES_OPERATOR_LICENSE_RECORD"]',
            '["FORMAL_IMPORT_REQUIRES_OPERATOR_LICENSE_RECORD","LICENSE_EVIDENCE_MISSING"]'
        )"""
    )


def downgrade() -> None:
    # License evidence is optional for user-supplied local paths, so a downgrade
    # cannot infer which reports should have been blocked without fabricating state.
    pass
