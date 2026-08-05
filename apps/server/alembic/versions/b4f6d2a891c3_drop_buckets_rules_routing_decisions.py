"""drop buckets, rules, routing_decisions

Revision ID: b4f6d2a891c3
Revises: e2a1c9f4d7b0
Create Date: 2026-08-05 00:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4f6d2a891c3'
down_revision: Union[str, Sequence[str], None] = 'e2a1c9f4d7b0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_table('routing_decisions')
    op.drop_table('rules')
    op.drop_table('buckets')


def downgrade() -> None:
    """Downgrade schema."""
    op.create_table('buckets',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('description', sa.String(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name', name='uq_buckets_name')
    )
    op.create_table('rules',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('bucket_id', sa.Integer(), nullable=False),
    sa.Column('rule_type', sa.String(), nullable=False),
    sa.Column('pattern', sa.String(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['bucket_id'], ['buckets.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('routing_decisions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('message_id', sa.Integer(), nullable=False),
    sa.Column('deciding_layer', sa.String(), nullable=False),
    sa.Column('bucket_id', sa.Integer(), nullable=True),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('reason', sa.String(), nullable=False),
    sa.Column('subject_person_entity_id', sa.Integer(), nullable=True),
    sa.Column('subject_raw_text', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['bucket_id'], ['buckets.id'], ),
    sa.ForeignKeyConstraint(['message_id'], ['messages.id'], ),
    sa.ForeignKeyConstraint(['subject_person_entity_id'], ['person_entities.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('message_id', name='uq_routing_decisions_message_id')
    )
