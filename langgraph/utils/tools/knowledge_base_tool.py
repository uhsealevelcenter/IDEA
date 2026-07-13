"""
Knowledge Base Tool
Queries a user's uploaded papers ("Knowledge" library) via PaperQA2
(multi-tenant), returning a formatted answer with citations and any
extracted figure/image paths.
"""

import asyncio
import base64
import hashlib
import json
import time as _time
from pathlib import Path
from typing import Any, Optional

import nest_asyncio
from langchain_core.tools import tool

from ..pqa.pqa_multi_tenant import get_user_settings, load_docs_from_disk, save_docs_to_disk

nest_asyncio.apply()

# In-memory Docs cache keyed by user_id -> {"docs": Docs, "revision": str}
_docs_cache: dict = {}

STATIC_DIR_NAME = "static"


def _save_base64_image(data_url: str, output_dir: Path, prefix: str = "kb_figure") -> Optional[Path]:
    """Save a base64 data URL to an image file. Returns the saved path or None."""
    try:
        if not data_url or not data_url.startswith("data:image"):
            return None

        header, b64_data = data_url.split(",", 1)
        mime_type = header.split(":")[1].split(";")[0]

        ext_map = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/gif": ".gif",
            "image/webp": ".webp",
        }
        ext = ext_map.get(mime_type, ".png")

        content_hash = hashlib.md5(b64_data.encode()).hexdigest()[:12]
        filename = f"{prefix}_{content_hash}{ext}"
        filepath = output_dir / filename

        if filepath.exists():
            return filepath

        image_data = base64.b64decode(b64_data)
        output_dir.mkdir(parents=True, exist_ok=True)
        filepath.write_bytes(image_data)
        return filepath
    except Exception as e:
        print(f"[PQA] Warning: Failed to save image: {e}")
        return None


async def _query_knowledge_base_async(query: str, user_id: str, session_id: Optional[str]) -> dict:
    global _docs_cache
    from paperqa import Docs
    from paperqa.agents.search import get_directory_index

    t_start = _time.perf_counter()

    print("[PQA] Step 1: Loading user settings...")
    settings = get_user_settings(user_id)
    print(f"[PQA] Settings loaded. LLM: {settings.llm}, Embedding: {settings.embedding}")

    print("[PQA] Step 2: Building/loading index...")
    t_idx = _time.perf_counter()
    index = await get_directory_index(settings=settings)
    print(f"[PQA] Index loaded in {_time.perf_counter() - t_idx:.2f}s.")

    index_files = await index.index_files
    if not index_files:
        return {"answer": "No papers found in your Knowledge base. Please upload papers first.", "images": []}
    print(f"[PQA] Found {len(index_files)} indexed files.")

    revision = hashlib.md5(str(sorted(index_files.keys())).encode()).hexdigest()
    cache_key = str(user_id)
    cached = _docs_cache.get(cache_key)

    if cached and cached["revision"] == revision:
        docs = cached["docs"]
        print("[PQA] Step 3: Reusing cached Docs object (in-memory cache hit).")
    else:
        disk_docs = load_docs_from_disk(user_id, revision)

        if disk_docs is not None:
            docs = disk_docs
            _docs_cache[cache_key] = {"docs": docs, "revision": revision}
            print("[PQA] Step 3: Loaded Docs from disk cache.")
        else:
            print("[PQA] Step 3: Building Docs object (no cache available)...")
            t_docs = _time.perf_counter()
            docs = Docs()
            paper_directory = settings.agent.index.paper_directory

            for file_path in index_files.keys():
                full_path = paper_directory / file_path
                if full_path.exists():
                    print(f"[PQA]   Adding: {file_path}")
                    await docs.aadd(full_path, settings=settings)

            _docs_cache[cache_key] = {"docs": docs, "revision": revision}
            save_docs_to_disk(user_id, docs, revision)
            print(f"[PQA] Docs built and cached in {_time.perf_counter() - t_docs:.2f}s.")

    print(f"[PQA] Step 4: Querying with: '{query}'...")
    t_query = _time.perf_counter()
    session = await docs.aquery(query=query, settings=settings)
    print(f"[PQA] Query complete in {_time.perf_counter() - t_query:.2f}s.")

    print("[PQA] Step 5: Extracting images from contexts...")
    static_dir = Path(STATIC_DIR_NAME)
    if session_id:
        output_dir = static_dir / str(user_id) / session_id / "pqa_media"
    else:
        output_dir = static_dir / str(user_id) / "pqa_media"

    saved_images = []
    seen_hashes: set = set()
    used_context_ids = getattr(session, "used_contexts", set())

    for context in session.contexts:
        is_used = context.id in used_context_ids if used_context_ids else True

        if not hasattr(context, "text") or not hasattr(context.text, "media"):
            continue

        for media in context.text.media:
            try:
                data_url = media.to_image_url()
                if not data_url:
                    continue

                if "," in data_url:
                    b64_part = data_url.split(",", 1)[1]
                    content_hash = hashlib.md5(b64_part.encode()).hexdigest()
                    if content_hash in seen_hashes:
                        continue
                    seen_hashes.add(content_hash)

                saved_path = _save_base64_image(data_url, output_dir)
                if saved_path:
                    info = getattr(media, "info", {}) or {}
                    page_num = info.get("page_num", info.get("page"))
                    media_type = info.get("type", "image")
                    description = info.get("enriched_description", "")

                    rel_path = saved_path.relative_to(static_dir)

                    saved_images.append({
                        "path": str(saved_path),
                        "relative_path": str(rel_path),
                        "page": page_num,
                        "type": media_type,
                        "description": description,
                        "used_in_answer": is_used,
                    })
                    print(f"[PQA] Saved: {saved_path.name} (page {page_num})")
            except Exception as e:
                print(f"[PQA] Warning: Failed to process media: {e}")
                continue

    print(f"[PQA] Extracted {len(saved_images)} unique images.")
    print(f"[PQA] Total query_knowledge_base time: {_time.perf_counter() - t_start:.2f}s")

    return {
        "answer": str(session),
        "images": saved_images,
    }


@tool
def query_knowledge_base_tool(query: str, user_id: str, session_id: str = "") -> str:
    """
    Query the user's uploaded "Knowledge" documents using PaperQA2.

    Use this when: reviewing scientific literature/documents in the
    Knowledge base; the query involves specific scientific methods,
    findings, or technical details; the answer requires citation from a
    primary source; or the user asks about figures/tables/images from
    papers. Enhance the user's query to be as detailed as possible.

    Access is limited to documents the user has uploaded via the
    "Knowledge" interface.

    Args:
        query: The question to ask about the papers.
        user_id: The user's ID (used to locate their paper library).
        session_id: Optional session ID, used to namespace saved figure
            images.

    Returns:
        A JSON string with "answer" (text with citations) and "images"
        (list of dicts with path/page/description for extracted figures;
        do not re-run OCR or extraction on these - use the description or
        view the image directly if needed).
    """
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    result = loop.run_until_complete(
        _query_knowledge_base_async(query, user_id, session_id or None)
    )
    return json.dumps(result, indent=2, default=str)
