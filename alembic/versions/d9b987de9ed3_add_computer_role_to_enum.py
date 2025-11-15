"""add_computer_role_to_enum

Revision ID: d9b987de9ed3
Revises: 1fc529effbe2
Create Date: 2025-08-25 01:14:05.625658

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd9b987de9ed3'
down_revision = '1fc529effbe2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add 'COMPUTER' to the existing messagerole enum
    op.execute("ALTER TYPE messagerole ADD VALUE 'COMPUTER'")


def downgrade() -> None:
    # Note: PostgreSQL doesn't support removing enum values directly
    # This would require recreating the enum and updating all references
    # For now, we'll leave the enum value in place
    pass