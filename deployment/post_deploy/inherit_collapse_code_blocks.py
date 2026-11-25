"""Clear saved Off values once so existing accounts inherit the IDEA default."""

import asyncio

from sqlalchemy import JSON, String, cast, func, literal, update
from sqlalchemy.dialects import postgresql, sqlite

from open_webui.internal.db import get_async_db
from open_webui.models.config import Config
from open_webui.models.users import User


MIGRATION_KEY = "idea.migrations.inherit_collapse_code_blocks.v1"
INTERFACE_DEFAULTS_KEY = "ui.default_interface_settings"


async def migrate() -> int | None:
    async with get_async_db() as db, db.begin():
        dialect = db.get_bind().dialect.name
        settings = User.__table__.c.settings
        if dialect == "sqlite":
            insert = sqlite.insert
            path = "$.ui.collapseCodeBlocks"
            is_off = func.json_type(settings, path) == "false"
            inherited_settings = func.json_remove(settings, path)
        elif dialect == "postgresql":
            insert = postgresql.insert
            path = postgresql.array(["ui", "collapseCodeBlocks"], type_=String)
            settings_jsonb = cast(settings, postgresql.JSONB)
            is_off = settings_jsonb.op("#>")(path) == cast(literal("false"), postgresql.JSONB)
            inherited_settings = cast(settings_jsonb.op("#-")(path), JSON)
        else:
            raise RuntimeError(f"Unsupported Open WebUI database dialect: {dialect}")

        # Claim the migration and update the preferences in the same transaction.
        # A concurrent deploy waits for the claim or skips it once committed.
        claimed = await db.execute(
            insert(Config.__table__)
            .values(key=MIGRATION_KEY, value=True)
            .on_conflict_do_nothing(index_elements=[Config.key])
            .returning(Config.key)
        )
        if claimed.scalar_one_or_none() is None:
            return None

        defaults = await db.get(Config, INTERFACE_DEFAULTS_KEY)
        if (
            defaults is None
            or not isinstance(defaults.value, dict)
            or defaults.value.get("collapseCodeBlocks") is not True
        ):
            raise RuntimeError("Set the Open WebUI collapse default before migrating users")

        result = await db.execute(
            update(User.__table__).where(is_off).values(settings=inherited_settings)
        )
        return result.rowcount


if __name__ == "__main__":
    count = asyncio.run(migrate())
    if count is None:
        print("Existing Open WebUI collapse preferences were already migrated.")
    else:
        print(f"{count} existing Open WebUI accounts now inherit the collapsed-blocks default.")
