"""add employees, relay_requests, person_entities.verified_employee; drop dead message classification columns

Revision ID: e2a1c9f4d7b0
Revises: 321361b652cc
Create Date: 2026-08-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e2a1c9f4d7b0'
down_revision: Union[str, Sequence[str], None] = '321361b652cc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('employees',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('employment_id', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('employment_id', name='uq_employees_employment_id')
    )
    op.create_table('relay_requests',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source_message_id', sa.Integer(), nullable=False),
    sa.Column('source_identity', sa.String(), nullable=False),
    sa.Column('target_identity', sa.String(), nullable=False),
    sa.Column('target_conversation_id', sa.String(), nullable=False),
    sa.Column('message_text', sa.String(), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['source_message_id'], ['messages.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('target_conversation_id', name='uq_relay_requests_target_conversation_id')
    )
    op.add_column('person_entities', sa.Column('verified_employee', sa.Boolean(), server_default=sa.false(), nullable=False))
    op.drop_column('messages', 'fine_bucket')
    op.drop_column('messages', 'classified_by')
    op.drop_column('messages', 'confidence')
    op.drop_column('messages', 'classified_at')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column('messages', sa.Column('classified_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('messages', sa.Column('confidence', sa.Float(), nullable=True))
    op.add_column('messages', sa.Column('classified_by', sa.String(), nullable=True))
    op.add_column('messages', sa.Column('fine_bucket', sa.String(), nullable=True))
    op.drop_column('person_entities', 'verified_employee')
    op.drop_table('relay_requests')
    op.drop_table('employees')
