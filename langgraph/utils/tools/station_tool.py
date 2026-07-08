"""
Station Info Tool
Looks up UHSLC tide gauge station id/name information (Fast Delivery
product) using an LLM grounded on the Station List Appendix.
"""

import os
from typing import Any, Optional

from langchain_core.tools import tool
from litellm import responses

from ..data.station_list_appendix import STATION_LIST_APPENDIX

STATION_LOOKUP_MODEL = "openai/gpt-4o-mini"


def _extract_text_from_station_response(response: Any) -> Optional[str]:
    if response is None or not hasattr(response, "output"):
        return None

    for item in response.output:
        if getattr(item, "type", None) == "message":
            for c in getattr(item, "content", []):
                if hasattr(c, "text"):
                    return c.text

    return None


@tool
def get_station_info_tool(station_query: str) -> str:
    """
    Look up UHSLC tide gauge station id/name information (Fast Delivery product).

    Always call this when a user requests specific tide gauge station
    information (uhslc_id and/or name), or an analysis for all stations in a
    region (e.g., "all Hawaii stations"). Never guess or infer a station id
    or name from memory.

    Args:
        station_query: A natural language query, a station name, or a
            station id (e.g., "Honolulu, HI", "057", "What stations are in
            Hawaii?").

    Returns:
        A text answer with the matched station id(s)/name(s), a
        clarification request if the query is ambiguous, or a note if no
        match was found.
    """
    response = responses(
        model=STATION_LOOKUP_MODEL,
        api_base=os.getenv("OPENAI_BASE_URL"),
        api_key=os.getenv("OPENAI_API_KEY"),
        input=[
            {"role": "system", "content": STATION_LIST_APPENDIX},
            {"role": "user", "content": station_query},
        ],
        stream=False,
    )
    return _extract_text_from_station_response(response) or "Not in FD station list."
