"""Add MCP connections table

Revision ID: 4a6f9e0bb0f4
Revises: 901f240d4e80
Create Date: 2025-02-05 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "4a6f9e0bb0f4"
down_revision = "901f240d4e80"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create enum type using raw SQL to avoid duplicate issues
    conn = op.get_bind()
    conn.execute(sa.text("""
        DO $$ BEGIN
            CREATE TYPE mcptransporttype AS ENUM ('streamable_http', 'sse', 'stdio');
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
    """))

    # Define the enum for column type reference (without creating it)
    from sqlalchemy.dialects.postgresql import ENUM
    transport_enum = ENUM(
        "streamable_http",
        "sse",
        "stdio",
        name="mcptransporttype",
        create_type=False,
    )

    op.create_table(
        "mcpconnection",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("transport", transport_enum, nullable=False),
        sa.Column("endpoint", sa.String(length=512), nullable=True),
        sa.Column("command", sa.String(length=512), nullable=True),
        sa.Column("command_args", sa.JSON(), nullable=True),
        sa.Column("headers", sa.JSON(), nullable=True),
        sa.Column("config", sa.JSON(), nullable=True),
        sa.Column("auth_token", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("last_connected_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"], name="fk_mcpconnection_user_id", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_mcpconnection_name", "mcpconnection", ["name"], unique=True)
    op.create_index("ix_mcpconnection_created_by", "mcpconnection", ["created_by"])


def downgrade() -> None:
    op.drop_index("ix_mcpconnection_created_by", table_name="mcpconnection")
    op.drop_index("ix_mcpconnection_name", table_name="mcpconnection")
    op.drop_table("mcpconnection")

    transport_enum = sa.Enum(
        "streamable_http",
        "sse",
        "stdio",
        name="mcptransporttype",
    )
    transport_enum.drop(op.get_bind(), checkfirst=True)
