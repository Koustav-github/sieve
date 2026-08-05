"""add platform_connections, departments, pending_verifications

Revision ID: c1d5e8a3f2b7
Revises: b4f6d2a891c3
Create Date: 2026-08-05 01:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c1d5e8a3f2b7'
down_revision: Union[str, Sequence[str], None] = 'b4f6d2a891c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('platform_connections',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('platform', sa.String(), nullable=False),
    sa.Column('connection_id', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('platform', name='uq_platform_connections_platform')
    )
    op.create_table('departments',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('team_name', sa.String(), nullable=False),
    sa.Column('lead_name', sa.String(), nullable=False),
    sa.Column('lead_email', sa.String(), nullable=False),
    sa.Column('platform_connection_id', sa.Integer(), nullable=False),
    sa.Column('channel_ref', sa.String(), nullable=False),
    sa.Column('requires_verification', sa.Boolean(), server_default=sa.true(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['platform_connection_id'], ['platform_connections.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('team_name', name='uq_departments_team_name')
    )
    op.create_table('pending_verifications',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('sender_handle', sa.String(), nullable=False),
    sa.Column('channel', sa.String(), nullable=False),
    sa.Column('target_department_id', sa.Integer(), nullable=False),
    sa.Column('message_text', sa.String(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['target_department_id'], ['departments.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('sender_handle', 'channel', name='uq_pending_verifications_sender_channel')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('pending_verifications')
    op.drop_table('departments')
    op.drop_table('platform_connections')
