"""
Web Search Tool
Performs a web search via LiteLLM's OpenAI-hosted web_search tool and
returns a summarized result with citation URLs.
"""

import json
import os
from typing import Any

from langchain_core.tools import tool
from litellm import responses
from idea_config import IDEA_AGENT_MODEL

WEB_SEARCH_MODEL = f"openai/{IDEA_AGENT_MODEL}"


def _extract_web_query_response(web_query_response: Any) -> dict:
    output_msg = next(
        (item for item in web_query_response.output if getattr(item, "type", None) == "message"),
        None,
    )
    if not output_msg:
        return {"content": None, "urls": []}

    texts, urls = [], []
    for part in getattr(output_msg, "content", []):
        if getattr(part, "text", None):
            texts.append(part.text)
            for ann in getattr(part, "annotations", []) or []:
                if getattr(ann, "type", "") == "url_citation":
                    urls.append({"title": ann.title, "url": ann.url})
    return {"content": "\n\n".join(texts) if texts else None, "urls": urls}


@tool
def web_search_tool(query: str) -> str:
    """
    Search the web for recent news, pages, or up-to-date information.

    Prefer this over manual/programmatic HTTP requests or scraping for
    general web discovery.

    Args:
        query: The search query.

    Returns:
        A JSON string with "content" (summarized text) and "urls" (list of
        {"title", "url"} citation dicts).
    """
    web_query_response = responses(
        model=WEB_SEARCH_MODEL,
        api_base=os.getenv("OPENAI_BASE_URL"),
        api_key=os.getenv("OPENAI_API_KEY"),
        reasoning={"effort": "low"},
        input=[
            {
                "role": "system",
                "content": "You are a concise research assistant that only searches the web and only responds with search results.",
            },
            {"role": "user", "content": query},
        ],
        tools=[{"type": "web_search"}],
        stream=False,
    )
    result = _extract_web_query_response(web_query_response)
    return json.dumps(result, indent=2)
