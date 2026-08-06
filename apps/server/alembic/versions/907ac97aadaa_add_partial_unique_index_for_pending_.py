"""add partial unique index for pending relay requests and department channel uniqueness

Revision ID: 907ac97aadaa
Revises: c1d5e8a3f2b7
Create Date: 2026-08-06 12:57:00.350844

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '907ac97aadaa'
down_revision: Union[str, Sequence[str], None] = 'c1d5e8a3f2b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(
        "uq_relay_requests_target_conversation_id", "relay_requests", type_="unique"
    )
    op.create_index(
        "uq_relay_requests_pending_target_conversation_id",
        "relay_requests",
        ["target_conversation_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_unique_constraint(
        "uq_departments_platform_connection_channel_ref",
        "departments",
        ["platform_connection_id", "channel_ref"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        "uq_departments_platform_connection_channel_ref", "departments", type_="unique"
    )
    op.drop_index("uq_relay_requests_pending_target_conversation_id", table_name="relay_requests")
    op.create_unique_constraint(
        "uq_relay_requests_target_conversation_id", "relay_requests", ["target_conversation_id"]
    )
