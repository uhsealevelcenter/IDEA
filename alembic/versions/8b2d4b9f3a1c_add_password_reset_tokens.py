"""Add password reset tokens table

Revision ID: 8b2d4b9f3a1c
Revises: 4a6f9e0bb0f4
Create Date: 2026-05-23 12:38:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "8b2d4b9f3a1c"
down_revision = "4a6f9e0bb0f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "passwordresettoken",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], name="fk_passwordresettoken_user_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_passwordresettoken_user_id", "passwordresettoken", ["user_id"])
    op.create_index("ix_passwordresettoken_token_hash", "passwordresettoken", ["token_hash"], unique=True)
    op.create_index("ix_passwordresettoken_expires_at", "passwordresettoken", ["expires_at"])
    op.create_index("ix_passwordresettoken_used_at", "passwordresettoken", ["used_at"])


def downgrade() -> None:
    op.drop_index("ix_passwordresettoken_used_at", table_name="passwordresettoken")
    op.drop_index("ix_passwordresettoken_expires_at", table_name="passwordresettoken")
    op.drop_index("ix_passwordresettoken_token_hash", table_name="passwordresettoken")
    op.drop_index("ix_passwordresettoken_user_id", table_name="passwordresettoken")
    op.drop_table("passwordresettoken")
