"""
Datetime Tool
Returns the current UTC date and time.
"""

from datetime import datetime, timedelta, timezone

from langchain_core.tools import tool


@tool
def get_datetime_tool() -> dict:
    """
    Get the current UTC date and time.

    Always call this whenever asked about the current date or time, instead
    of estimating or computing it manually.

    Returns:
        A dictionary with:
            - "iso": ISO 8601 formatted timestamp.
            - "human": Human-readable "YYYY-MM-DD HH:MM:SS UTC" timestamp.
    """
    now_utc = datetime.now(timezone.utc)
    if now_utc.microsecond >= 500_000:
        now_utc += timedelta(seconds=1)
    now_utc = now_utc.replace(microsecond=0)
    return {
        "iso": now_utc.isoformat(),
        "human": now_utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
    }
